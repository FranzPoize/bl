from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import bl

copier_stub = ModuleType("copier")
copier_stub.run_copy = lambda *args, **kwargs: None
sys.modules.setdefault("copier", copier_stub)
import bl.__main__ as bl_main  # noqa: E402 -- install the Copier stub before importing the CLI


def test_check_last_version_queries_pip_and_warns_when_older(monkeypatch):
    calls = []
    printed = []

    def fake_run(args, capture_output):
        calls.append(args)
        if args == ["which", "pip"]:
            return subprocess.CompletedProcess(args, 0, stdout=b"/usr/bin/pip\n")
        assert args == ["pip", "index", "versions", "--json", "bl-odoo"]
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"versions": ["9.9.9"]}).encode())

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(bl, "__version__", "0.0.1")
    monkeypatch.setattr(bl_main.out_console, "print", lambda message: printed.append(message))

    bl_main.check_last_version()

    assert calls == [["which", "pip"], ["pip", "index", "versions", "--json", "bl-odoo"]]
    assert printed
    assert "Yours is 0.0.1" in printed[0]
    assert "Last is 9.9.9" in printed[0]


@pytest.mark.parametrize(
    ("argv", "expected_checks"),
    [(["bl", "init", "dest"], 1), (["bl", "init", "-N", "dest"], 0)],
)
def test_run_checks_version_for_commands_unless_disabled(monkeypatch, argv, expected_checks):
    checks = []
    copies = []

    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(bl_main, "setup_logging", lambda level: None)
    monkeypatch.setattr(bl_main, "check_last_version", lambda: checks.append(True))
    monkeypatch.setattr(bl_main, "run_copy", lambda template, destination: copies.append((template, destination)))

    with pytest.raises(SystemExit) as exc:
        bl_main.run()

    assert exc.value.code == 0
    assert len(checks) == expected_checks
    assert copies[0][1] == Path("dest")


def test_run_dispatches_edit_command(monkeypatch, tmp_path: Path):
    calls = []

    async def fake_make_editable(repository_name, config, workdir):
        calls.append((repository_name, config, workdir))

    monkeypatch.setattr(
        sys, "argv", ["bl", "edit", "test-repo", "-N", "-c", str(tmp_path / "spec.yaml"), "-w", str(tmp_path)]
    )
    monkeypatch.setattr(bl_main, "setup_logging", lambda level: None)
    monkeypatch.setattr(bl_main, "load_spec_file", lambda *args: SimpleNamespace(repos={}, workdir=tmp_path))
    monkeypatch.setattr(bl_main, "make_editable", fake_make_editable)

    bl_main.run()

    assert calls == [(Path("test-repo"), tmp_path / "spec.yaml", tmp_path)]


@pytest.mark.parametrize("local_odoo", [False, True])
def test_run_dispatches_build_with_local_odoo(monkeypatch, tmp_path: Path, local_odoo: bool):
    calls = []
    spec = SimpleNamespace(repos={}, workdir=tmp_path)

    async def build(project_spec, **options):
        calls.append((project_spec, options))

    argv = ["bl", "build", "-N"]
    if local_odoo:
        argv.append("--local-odoo")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(bl_main, "setup_logging", lambda _: None)
    monkeypatch.setattr(bl_main, "load_spec_file", lambda *args: spec)
    monkeypatch.setattr(bl_main, "process_project", build)

    bl_main.run()

    assert calls == [(spec, {"concurrency": 28, "use_bindfs": False, "local_odoo": local_odoo})]


@pytest.mark.parametrize("option", ["--dry-run", "--force", "decline"])
def test_clean_store_does_not_require_project_spec(monkeypatch, tmp_path: Path, option: str):
    calls = []

    async def prune(root, *, dry_run):
        calls.append((root, dry_run))
        return [root / "worktrees" / "unused"]

    argv = ["bl", "clean-store", "-N"]
    if option != "decline":
        argv.append(option)
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(bl_main, "setup_logging", lambda _: None)
    monkeypatch.setattr(bl_main, "get_odoo_store_root", lambda: tmp_path)
    monkeypatch.setattr(bl_main, "prune_unused_worktrees", prune)
    monkeypatch.setattr(
        bl_main, "load_spec_file", lambda *args: pytest.fail("Store cleanup must not load a project spec")
    )
    monkeypatch.setattr("builtins.input", lambda _: "n")

    bl_main.run()

    assert calls == ([(tmp_path, True), (tmp_path, False)] if option == "--force" else [(tmp_path, True)])


@pytest.mark.parametrize(
    "relative_directory",
    ["external-src/test-repo", "external-src/test-repo/module", "external-src/test-repo/module/models/deep"],
)
def test_edit_dot_makes_containing_repo_editable(monkeypatch, tmp_path, relative_directory):
    from bl import config as bl_config
    from bl import editable

    project = tmp_path / "project" / "odoo"
    directory = project / relative_directory
    directory.mkdir(parents=True)
    (project / "spec.yaml").write_text("test-repo: {}\nother-repo: {}\n")
    calls = []

    async def fake_run_git(*args, cwd):
        calls.append((args, cwd))
        return 0, "", ""

    async def fake_remove_hook(path):
        calls.append(("remove-hook", path))
        return 0, "", ""

    monkeypatch.chdir(directory)
    monkeypatch.setattr(sys, "argv", ["bl", "edit", ".", "-N"])
    monkeypatch.setattr(bl_main, "setup_logging", lambda level: None)
    monkeypatch.setattr(editable, "run_git", fake_run_git)
    monkeypatch.setattr(editable, "remove_locking_pre_commit", fake_remove_hook)
    monkeypatch.setattr(bl_config, "xdg_config_home", lambda: tmp_path / "xdg")

    bl_main.run()

    assert calls
    assert all(path == project / "external-src" / "test-repo" for _, path in calls)
    assert ("remove-hook", project / "external-src" / "test-repo") in calls
    assert bl_config.load_config("project")["editable"] == {"test-repo": "True"}


@pytest.mark.parametrize("explicit_config", [False, True])
def test_edit_dot_respects_target_folder_and_explicit_options(monkeypatch, tmp_path, explicit_config):
    project = tmp_path / "project"
    directory = project / "custom" / "checkout" / "module"
    directory.mkdir(parents=True)
    spec = tmp_path / "custom.yaml" if explicit_config else project / "spec.yaml"
    spec.write_text("repo-alias:\n  target_folder: custom/checkout\n")
    calls = []

    async def fake_make_editable(*args):
        calls.append(args)

    argv = ["bl", "edit", ".", "-N"]
    if explicit_config:
        # Even a nearer default spec must not override an explicit config.
        (directory / "spec.yaml").write_text("unrelated: {}\n")
        argv += ["-c", os.path.relpath(spec, directory), "-w", str(project)]
    monkeypatch.chdir(directory)
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(bl_main, "setup_logging", lambda level: None)
    monkeypatch.setattr(bl_main, "make_editable", fake_make_editable)

    bl_main.run()

    assert len(calls) == 1
    name, config, workdir = calls[0]
    assert name == "repo-alias"
    assert config.resolve() == spec
    assert workdir == (project if explicit_config else None)


@pytest.mark.parametrize("failure", ["missing-spec", "too-deep", "outside-repo", "nearest-spec"])
def test_edit_dot_reports_discovery_errors_without_editing(monkeypatch, tmp_path, capsys, failure):
    project = tmp_path / "project"
    directory = project / "external-src" / "test-repo" / "module"
    if failure == "too-deep":
        directory = directory / "a" / "b" / "c"
    elif failure == "outside-repo":
        directory = project
    directory.mkdir(parents=True)
    if failure != "missing-spec":
        (project / "spec.yaml").write_text("test-repo: {}\n")
    if failure == "nearest-spec":
        (directory.parent / "spec.yaml").write_text("unrelated: {}\n")

    async def unexpected_edit(*args):
        pytest.fail("Discovery failure must not make any repository editable")

    monkeypatch.chdir(directory)
    monkeypatch.setattr(sys, "argv", ["bl", "edit", ".", "-N"])
    monkeypatch.setattr(bl_main, "setup_logging", lambda level: None)
    monkeypatch.setattr(bl_main, "make_editable", unexpected_edit)

    with pytest.raises(SystemExit) as exc:
        bl_main.run()

    assert exc.value.code == 2
    expected = "No spec.yaml found" if failure in ("missing-spec", "too-deep") else "not inside a repository"
    assert expected in capsys.readouterr().err


def test_edit_dot_finds_spec_through_shell_symlink_path(monkeypatch, tmp_path):
    checkout = tmp_path / "store" / "repo"
    (checkout / "module").mkdir(parents=True)
    project = tmp_path / "project"
    (project / "external-src").mkdir(parents=True)
    link = project / "external-src" / "test-repo"
    link.symlink_to(checkout, target_is_directory=True)
    (project / "spec.yaml").write_text("test-repo: {}\n")
    calls = []

    async def fake_make_editable(*args):
        calls.append(args)

    monkeypatch.chdir(link / "module")
    monkeypatch.setenv("PWD", str(link / "module"))
    monkeypatch.setattr(sys, "argv", ["bl", "edit", ".", "-N"])
    monkeypatch.setattr(bl_main, "setup_logging", lambda level: None)
    monkeypatch.setattr(bl_main, "make_editable", fake_make_editable)

    bl_main.run()

    assert calls == [("test-repo", project / "spec.yaml", None)]
