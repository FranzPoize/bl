from __future__ import annotations

import json
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
