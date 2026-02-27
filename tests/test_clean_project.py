from __future__ import annotations

from pathlib import Path

import pytest

from bl.clean_project import (
    _clean_directory,
    _clean_dirty_repos,
    gather_dirty_repo_info,
    reset_dirty_repos,
    clean_project,
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
    # Directory may or may not remain, but we only care about failure flag here.


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


def test_clean_directory_interactive_error_after_confirmation(monkeypatch, tmp_path: Path) -> None:
    import shutil

    target = tmp_path / "to_delete"
    target.mkdir()

    monkeypatch.setattr("builtins.input", lambda _: "y")

    def failing_rmtree(path: Path) -> None:
        raise OSError("boom")

    monkeypatch.setattr(shutil, "rmtree", failing_rmtree)

    failed = _clean_directory(target, non_interactive=False)
    assert failed is True


def test_clean_project_non_interactive_removes_dirs(tmp_path: Path) -> None:
    workdir = tmp_path
    src = workdir / "src"
    external = workdir / "external-src"
    src.mkdir()
    external.mkdir()

    project = ProjectSpec(repos={}, workdir=workdir)
    ret = clean_project(project, non_interactive=True)
    assert ret == 0
    assert not src.exists()
    assert not external.exists()


def test_clean_project_non_interactive_partial_failure(monkeypatch, tmp_path: Path) -> None:
    import shutil

    workdir = tmp_path
    src = workdir / "src"
    external = workdir / "external-src"
    src.mkdir()
    external.mkdir()

    real_rmtree = shutil.rmtree

    def fake_rmtree(path: Path) -> None:
        if path == src.resolve():
            raise OSError("boom")
        real_rmtree(path)

    monkeypatch.setattr(shutil, "rmtree", fake_rmtree)

    project = ProjectSpec(repos={}, workdir=workdir)
    ret = clean_project(project, non_interactive=True)
    # One deletion failed -> clean_project should signal failure
    assert ret == 1


@pytest.mark.asyncio
async def test_clean_dirty_repos_no_failures(monkeypatch, tmp_path: Path) -> None:
    project = ProjectSpec(repos={}, workdir=tmp_path)

    dirty_infos = ["dummy"]
    gathered_specs: list[ProjectSpec] = []
    reset_calls: list[tuple[ProjectSpec, list[str]]] = []

    async def fake_gather_dirty_repo_info(spec: ProjectSpec) -> list[str]:
        gathered_specs.append(spec)
        return dirty_infos

    async def fake_reset_dirty_repos(spec: ProjectSpec, infos: list[str]) -> bool:
        reset_calls.append((spec, infos))
        return False

    monkeypatch.setattr("bl.clean_project.gather_dirty_repo_info", fake_gather_dirty_repo_info)
    monkeypatch.setattr("bl.clean_project.reset_dirty_repos", fake_reset_dirty_repos)

    result = await _clean_dirty_repos(project)

    assert result is False
    assert gathered_specs == [project]
    assert reset_calls == [(project, dirty_infos)]


@pytest.mark.asyncio
async def test_clean_dirty_repos_propagates_failure(monkeypatch, tmp_path: Path) -> None:
    project = ProjectSpec(repos={}, workdir=tmp_path)

    async def fake_gather_dirty_repo_info(spec: ProjectSpec) -> list[str]:
        return []

    async def fake_reset_dirty_repos(spec: ProjectSpec, infos: list[str]) -> bool:
        return True

    monkeypatch.setattr("bl.clean_project.gather_dirty_repo_info", fake_gather_dirty_repo_info)
    monkeypatch.setattr("bl.clean_project.reset_dirty_repos", fake_reset_dirty_repos)

    result = await _clean_dirty_repos(project)

    assert result is True


@pytest.mark.asyncio
async def test_gather_dirty_repo_info_filters_by_git_and_status(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path

    # Two repos: one without .git, one with .git and dirty status.
    repo_a = _make_repo_info()
    repo_b = _make_repo_info()
    project = ProjectSpec(repos={"a": repo_a, "b": repo_b}, workdir=workdir)

    module_a = workdir / "a"
    module_b = workdir / "b"
    module_a.mkdir()
    module_b.mkdir()
    (module_b / ".git").mkdir()

    # Make get_module_path deterministic for the test.
    def fake_get_module_path(wd: Path, name: str, repo_info: RepoInfo) -> Path:  # type: ignore[override]
        return wd / name

    calls: list[Path] = []

    async def fake_run_git(*args: str, cwd: Path | None = None):
        assert cwd is not None
        calls.append(cwd)
        # Pretend repo "b" is dirty, "a" is never queried because it lacks .git
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
    # Only the git repo should have triggered a git status call.
    assert calls == [module_b]


@pytest.mark.asyncio
async def test_reset_dirty_repos_honors_user_choice_and_counts(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path
    module_path = workdir / "mod"
    git_dir = module_path / ".git"
    git_dir.mkdir(parents=True)

    repo_info = _make_repo_info()
    dirty_infos = [("mod", repo_info, " M file.txt", module_path)]
    project = ProjectSpec(repos={"mod": repo_info}, workdir=workdir)

    # Always answer "n" to cleaning; reset_repo should never be called.
    answers: list[str] = []

    def fake_input(prompt: str) -> str:
        answers.append(prompt)
        return "n"

    reset_calls: list[Path] = []

    async def fake_reset_repo(path: Path):
        reset_calls.append(path)
        return 0, "", ""

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("bl.clean_project.reset_repo", fake_reset_repo)

    failed = await reset_dirty_repos(project, dirty_infos)

    # No failures should be reported and reset_repo should not have been called.
    assert failed == 0
    assert reset_calls == []
    assert any("Clean repo mod" in prompt for prompt in answers)

