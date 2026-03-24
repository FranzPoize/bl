from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bl.clean_project import (
    _clean_directory,
    gather_dirty_repo_info,
    clean_project,
    show_diffs,
)
from bl.types import ProjectSpec, RepoInfo


def _make_repo_info() -> RepoInfo:
    return RepoInfo(
        modules=[],
        remotes={},
        refspecs=[],
        shell_commands=[],
        patch_globs_to_apply=[],
        target_folder=None,
        locales=[],
    )


def test_clean_directory_non_interactive_missing_dir(tmp_path: Path) -> None:
    target = tmp_path / "missing"
    failed = _clean_directory(target, non_interactive=True)
    assert failed is False
    # Directory should still not exist
    assert not target.exists()


def test_clean_directory_non_interactive_success(tmp_path: Path) -> None:
    target = tmp_path / "to_delete"
    target.mkdir()
    failed = _clean_directory(target, non_interactive=True)
    assert failed is False
    assert not target.exists()


def test_clean_directory_non_interactive_failure(monkeypatch, tmp_path: Path) -> None:
    import shutil

    target = tmp_path / "to_delete"
    target.mkdir()

    def fake_rmtree(path: Path) -> None:
        raise OSError("boom")

    monkeypatch.setattr(shutil, "rmtree", fake_rmtree)

    failed = _clean_directory(target, non_interactive=True)
    assert failed is True


def test_clean_directory_interactive_yes(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "to_delete"
    target.mkdir()

    monkeypatch.setattr("builtins.input", lambda _: "y")

    failed = _clean_directory(target, non_interactive=False)
    assert failed is False
    assert not target.exists()


def test_clean_directory_interactive_no(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "to_keep"
    target.mkdir()

    monkeypatch.setattr("builtins.input", lambda _: "n")

    failed = _clean_directory(target, non_interactive=False)
    assert failed is False
    # Directory should not be deleted
    assert target.exists()


@pytest.mark.asyncio
async def test_clean_project_remove_flag(tmp_path: Path) -> None:
    workdir = tmp_path
    src = workdir / "src"
    external = workdir / "external-src"
    src.mkdir()
    external.mkdir()

    project = ProjectSpec(repos={}, workdir=workdir)
    # By default, clean_project (with no flags) just checks for dirty repos.
    ret = await clean_project(project)
    assert ret == 0
    assert src.exists()
    assert external.exists()

    # With remove=True, it should delete them.
    ret = await clean_project(project, remove=True, force=True)
    assert ret == 0
    assert not src.exists()
    assert not external.exists()


@pytest.mark.asyncio
async def test_clean_project_unlink_flag(tmp_path: Path) -> None:
    workdir = tmp_path
    links = workdir / "links"
    links.mkdir()
    (links / "l1").symlink_to(workdir)

    project = ProjectSpec(repos={}, workdir=workdir)
    ret = await clean_project(project, unlink=True)
    assert ret == 0
    assert not links.exists()


@pytest.mark.asyncio
async def test_clean_project_dry_run(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path
    src = workdir / "src"
    src.mkdir()

    project = ProjectSpec(repos={}, workdir=workdir)

    # In dry_run, it should NOT delete anything even if remove=True.
    ret = await clean_project(project, remove=True, force=True, dry_run=True)
    assert ret == 0
    assert src.exists()


@pytest.mark.asyncio
async def test_gather_dirty_repo_info_filters_by_git_and_status(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path

    repo_a = _make_repo_info()
    repo_b = _make_repo_info()
    project = ProjectSpec(repos={"a": repo_a, "b": repo_b}, workdir=workdir)

    module_a = workdir / "a"
    module_b = workdir / "b"
    module_a.mkdir()
    module_b.mkdir()
    (module_b / ".git").mkdir()

    def fake_get_module_path(wd: Path, name: str, repo_info: RepoInfo) -> Path:
        return wd / name

    calls: list[Path] = []

    async def fake_run_git(*args: str, cwd: Path | None = None):
        assert cwd is not None
        calls.append(cwd)
        return 0, " M file.txt" if cwd == module_b else "", ""

    monkeypatch.setattr("bl.clean_project.get_module_path", fake_get_module_path)
    monkeypatch.setattr("bl.clean_project.run_git", fake_run_git)

    dirty_infos = await gather_dirty_repo_info(project)

    assert len(dirty_infos) == 1
    name, repo_info, out, module_path = dirty_infos[0]
    assert name == "b"
    assert repo_info is repo_b
    assert "file.txt" in out
    assert module_path == module_b
    assert calls == [module_b]


@pytest.mark.asyncio
async def test_show_diffs(monkeypatch, tmp_path: Path) -> None:
    from bl.spec_processor import console

    workdir = tmp_path
    repo_info = _make_repo_info()
    project = ProjectSpec(repos={"mod": repo_info}, workdir=workdir)

    module_path = workdir / "mod"
    module_path.mkdir()
    (module_path / ".git").mkdir()

    async def fake_run_git(*args: str, cwd: Path | None = None):
        if "status" in args:
            return 0, " M file.txt", ""
        if "diff" in args:
            if "--cached" in args:
                return 0, "staged diff content", ""
            return 0, "unstaged diff content", ""
        return 0, "", ""

    monkeypatch.setattr("bl.clean_project.run_git", fake_run_git)
    monkeypatch.setattr("bl.clean_project.get_module_path", lambda *args: module_path)

    printed_content = []

    def fake_print(content: Any):
        printed_content.append(str(content))

    monkeypatch.setattr(console, "print", fake_print)

    await show_diffs(project)

    assert any("unstaged diff content" in s for s in printed_content)
    assert any("staged diff content" in s for s in printed_content)
