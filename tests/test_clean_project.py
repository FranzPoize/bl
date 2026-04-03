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
    assert not (links / "l1").exists()


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
    repo_c = _make_repo_info()
    project = ProjectSpec(repos={"a": repo_a, "b": repo_b, "c": repo_c}, workdir=workdir)

    module_a = workdir / "a"
    module_b = workdir / "b"
    module_c = workdir / "c"
    module_a.mkdir()
    module_b.mkdir()
    module_c.mkdir()
    (module_b / ".git").mkdir()
    (module_c / ".git").mkdir()

    def fake_get_module_path(wd: Path, name: str, repo_info: RepoInfo) -> Path:
        return wd / name

    calls: list[Path] = []

    async def fake_run_git(*args: str, cwd: Path | None = None):
        assert cwd is not None
        calls.append(cwd)
        # a: no .git (skipped)
        # b: dirty (non-empty output)
        # c: clean (empty output - branch 71->61)
        if cwd == module_b:
            return 0, " M file.txt", ""
        if cwd == module_c:
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr("bl.clean_project.get_module_path", fake_get_module_path)
    monkeypatch.setattr("bl.clean_project.run_git", fake_run_git)

    dirty_infos = await gather_dirty_repo_info(project)

    # Only repo b is dirty
    assert len(dirty_infos) == 1
    name, repo_info, out, module_path = dirty_infos[0]
    assert name == "b"
    assert module_path == module_b

    # Both b and c were checked (a was skipped due to no .git)
    assert module_b in calls
    assert module_c in calls
    assert module_a not in calls


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


@pytest.mark.asyncio
async def test_show_diffs_empty_output(monkeypatch, tmp_path: Path) -> None:
    """Test show_diffs handles empty diff output gracefully."""
    from bl.clean_project import show_diffs
    from bl.spec_processor import console

    workdir = tmp_path
    repo_info = _make_repo_info()
    project = ProjectSpec(repos={"mod": repo_info}, workdir=workdir)

    module_path = workdir / "mod"
    module_path.mkdir()
    (module_path / ".git").mkdir()

    async def fake_run_git(*args, cwd=None):
        if "status" in args:
            return 0, " M file.txt", ""  # Dirty repo
        if "diff" in args:
            return 0, "", ""  # Empty diff output for both unstaged and staged
        return 0, "", ""

    monkeypatch.setattr("bl.clean_project.run_git", fake_run_git)
    monkeypatch.setattr("bl.clean_project.get_module_path", lambda *args: module_path)

    printed_content = []

    def fake_print(content: Any):
        printed_content.append(str(content))

    monkeypatch.setattr(console, "print", fake_print)

    # Should not raise, just no output for empty diffs
    await show_diffs(project)

    # Only the dirty repo message should appear, not the diff headers
    assert not any("Diff for" in s for s in printed_content)
    assert not any("Staged diff for" in s for s in printed_content)


@pytest.mark.asyncio
async def test_clean_directory_interactive_oserror(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "to_delete"
    target.mkdir()
    monkeypatch.setattr("builtins.input", lambda _: "y")

    import shutil

    def fake_rmtree(path):
        raise OSError("boom")

    monkeypatch.setattr(shutil, "rmtree", fake_rmtree)
    failed = _clean_directory(target, non_interactive=False)
    assert failed is True


@pytest.mark.asyncio
async def test_handle_dirty_repos_dirty_repo(monkeypatch, tmp_path: Path) -> None:
    from bl.clean_project import handle_dirty_repos
    from bl.spec_processor import console

    workdir = tmp_path
    repo_info = _make_repo_info()
    project = ProjectSpec(repos={"mod": repo_info}, workdir=workdir)

    module_path = workdir / "mod"
    module_path.mkdir()
    (module_path / ".git").mkdir()

    async def fake_run_git(*args, cwd=None):
        if "status" in args:
            return 0, " M file.txt", ""
        return 0, "", ""

    monkeypatch.setattr("bl.clean_project.run_git", fake_run_git)
    monkeypatch.setattr("bl.clean_project.get_module_path", lambda *args: module_path)

    printed = []
    monkeypatch.setattr(console, "print", lambda x: printed.append(str(x)))
    monkeypatch.setattr("builtins.input", lambda x: "n")

    ret = await handle_dirty_repos(project, dry_run=False)
    assert ret == 1
    assert any("dirty repositories" in str(p).lower() for p in printed)


@pytest.mark.asyncio
async def test_handle_dirty_repos_reset_success(monkeypatch, tmp_path: Path) -> None:
    """Test that user answering 'y' triggers repo reset and returns success."""
    from bl.clean_project import handle_dirty_repos, reset_repo
    from bl.spec_processor import console

    workdir = tmp_path
    repo_info = _make_repo_info()
    project = ProjectSpec(repos={"mod": repo_info}, workdir=workdir)

    module_path = workdir / "mod"
    module_path.mkdir()
    (module_path / ".git").mkdir()

    reset_called = []

    async def fake_run_git(*args, cwd=None):
        if "status" in args:
            return 0, " M file.txt", ""
        if "reset" in args:
            reset_called.append(cwd)
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr("bl.clean_project.run_git", fake_run_git)
    monkeypatch.setattr("bl.clean_project.get_module_path", lambda *args: module_path)

    printed = []
    monkeypatch.setattr(console, "print", lambda x: printed.append(str(x)))
    monkeypatch.setattr("builtins.input", lambda x: "y")

    ret = await handle_dirty_repos(project, dry_run=False)
    assert ret == 0
    assert len(reset_called) == 1
    assert reset_called[0] == module_path


@pytest.mark.asyncio
async def test_handle_dirty_repos_reset_failure(monkeypatch, tmp_path: Path) -> None:
    """Test that reset failure returns 1."""
    from bl.clean_project import handle_dirty_repos
    from bl.spec_processor import console

    workdir = tmp_path
    repo_info = _make_repo_info()
    project = ProjectSpec(repos={"mod": repo_info}, workdir=workdir)

    module_path = workdir / "mod"
    module_path.mkdir()
    (module_path / ".git").mkdir()

    async def fake_run_git(*args, cwd=None):
        if "status" in args:
            return 0, " M file.txt", ""
        if "reset" in args:
            return 1, "", "reset failed"
        return 0, "", ""

    monkeypatch.setattr("bl.clean_project.run_git", fake_run_git)
    monkeypatch.setattr("bl.clean_project.get_module_path", lambda *args: module_path)

    printed = []
    monkeypatch.setattr(console, "print", lambda x: printed.append(str(x)))
    monkeypatch.setattr("builtins.input", lambda x: "y")

    ret = await handle_dirty_repos(project, dry_run=False)
    assert ret == 1
    assert any("Failed to reset" in str(p) for p in printed)


@pytest.mark.asyncio
async def test_handle_dirty_repos_dry_run(monkeypatch, tmp_path: Path) -> None:
    from bl.clean_project import handle_dirty_repos
    from bl.spec_processor import console
    from unittest.mock import AsyncMock

    workdir = tmp_path
    repo_info = _make_repo_info()
    project = ProjectSpec(repos={"mod": repo_info}, workdir=workdir)

    module_path = workdir / "mod"
    module_path.mkdir()
    (module_path / ".git").mkdir()

    async def fake_run_git(*args, cwd=None):
        if "status" in args:
            return 0, " M file.txt", ""
        return 0, "", ""

    monkeypatch.setattr("bl.clean_project.run_git", fake_run_git)
    monkeypatch.setattr("bl.clean_project.get_module_path", lambda *args: module_path)

    printed = []
    monkeypatch.setattr(console, "print", lambda x: printed.append(str(x)))

    ret = await handle_dirty_repos(project, dry_run=True)
    assert ret == 0


@pytest.mark.asyncio
async def test_clean_project_no_flags_uses_dirty_check(monkeypatch, tmp_path: Path) -> None:
    from bl.clean_project import clean_project
    from bl.spec_processor import console
    from unittest.mock import AsyncMock

    workdir = tmp_path
    src = workdir / "src"
    src.mkdir()
    (src / ".git").mkdir()

    project = ProjectSpec(repos={}, workdir=workdir)

    async def fake_run_git(*args, cwd=None):
        return 0, "", ""

    monkeypatch.setattr("bl.clean_project.run_git", fake_run_git)

    printed = []
    monkeypatch.setattr(console, "print", lambda x: printed.append(str(x)))

    ret = await clean_project(project)
    assert ret == 0
    assert any("No dirty repositories" in str(p) for p in printed)


@pytest.mark.asyncio
async def test_reset_repo_success(monkeypatch, tmp_path: Path) -> None:
    from bl.clean_project import reset_repo

    module_path = tmp_path / "repo"
    module_path.mkdir()

    async def fake_run_git(*args, cwd=None):
        return 0, "", ""

    monkeypatch.setattr("bl.clean_project.run_git", fake_run_git)

    ret, out, err = await reset_repo(module_path)
    assert ret == 0


@pytest.mark.asyncio
async def test_reset_repo_index_lock(monkeypatch, tmp_path: Path) -> None:
    from bl.clean_project import reset_repo

    module_path = tmp_path / "repo"
    module_path.mkdir()

    async def fake_run_git(*args, cwd=None):
        if "reset" in args:
            return 1, "", "error: cannot lock index file index.lock: File exists"
        return 0, "", ""

    monkeypatch.setattr("bl.clean_project.run_git", fake_run_git)

    ret, out, err = await reset_repo(module_path)
    assert ret == 1


@pytest.mark.asyncio
async def test_reset_repo_other_error(monkeypatch, tmp_path: Path) -> None:
    from bl.clean_project import reset_repo

    module_path = tmp_path / "repo"
    module_path.mkdir()

    async def fake_run_git(*args, cwd=None):
        if "reset" in args:
            return 1, "", "some other error"
        return 0, "", ""

    monkeypatch.setattr("bl.clean_project.run_git", fake_run_git)

    ret, out, err = await reset_repo(module_path)
    assert ret == -1


@pytest.mark.asyncio
async def test_handle_unlink_links_path_does_not_exist(tmp_path: Path) -> None:
    from bl.clean_project import handle_unlink

    workdir = tmp_path
    ret = await handle_unlink(workdir, dry_run=False)
    assert ret == 0


@pytest.mark.asyncio
async def test_handle_unlink_dry_run(tmp_path: Path) -> None:
    from bl.clean_project import handle_unlink

    workdir = tmp_path
    links = workdir / "links"
    links.mkdir()

    ret = await handle_unlink(workdir, dry_run=True)
    assert ret == 0
    assert links.exists()


@pytest.mark.asyncio
async def test_handle_unlink_failure(monkeypatch, tmp_path: Path) -> None:
    from bl.clean_project import handle_unlink
    from bl.spec_processor import console

    workdir = tmp_path
    links = workdir / "links"
    links.mkdir()
    (links / "link1").symlink_to(tmp_path / "target")

    async def fake_unlink_path(path):
        return 1, "some error"

    monkeypatch.setattr("bl.clean_project.unlink_path", fake_unlink_path)

    printed = []
    monkeypatch.setattr(console, "print", lambda x: printed.append(str(x)))

    ret = await handle_unlink(workdir, dry_run=False)
    assert ret == 1


@pytest.mark.asyncio
async def test_handle_remove_dry_run_shows_existing(tmp_path: Path, monkeypatch) -> None:
    from bl.clean_project import handle_remove
    from bl.spec_processor import console

    workdir = tmp_path
    src = workdir / "src"
    src.mkdir()

    printed = []
    monkeypatch.setattr(console, "print", lambda x: printed.append(str(x)))

    ret = await handle_remove(workdir, force=True, dry_run=True)
    assert ret == 0
    assert src.exists()


@pytest.mark.asyncio
async def test_handle_remove_failure(monkeypatch, tmp_path: Path) -> None:
    """Test that deletion failure returns 1."""
    from bl.clean_project import handle_remove, _clean_directory
    from bl.spec_processor import console

    workdir = tmp_path
    src = workdir / "src"
    src.mkdir()

    # Mock _clean_directory to return True (failure)
    monkeypatch.setattr("bl.clean_project._clean_directory", lambda path, non_interactive: True)

    printed = []
    monkeypatch.setattr(console, "print", lambda x: printed.append(str(x)))

    ret = await handle_remove(workdir, force=True, dry_run=False)
    assert ret == 1
    assert src.exists()  # Not deleted because deletion failed


@pytest.mark.asyncio
async def test_clean_project_handles_dirty_repos_failure(monkeypatch, tmp_path: Path) -> None:
    """Test that handle_dirty_repos failure propagates to clean_project return."""
    from bl.clean_project import clean_project, handle_dirty_repos

    workdir = tmp_path
    project = ProjectSpec(repos={}, workdir=workdir)

    # Mock handle_dirty_repos to return 1 (failure) - must be async
    async def mock_handle_dirty_repos(spec, dry_run):
        return 1

    monkeypatch.setattr("bl.clean_project.handle_dirty_repos", mock_handle_dirty_repos)

    ret = await clean_project(project, remove=False)
    assert ret == 1


@pytest.mark.asyncio
async def test_clean_project_handles_unlink_failure(monkeypatch, tmp_path: Path) -> None:
    """Test that handle_unlink failure propagates to clean_project return."""
    from bl.clean_project import clean_project, handle_unlink

    workdir = tmp_path
    project = ProjectSpec(repos={}, workdir=workdir)

    # Mock handle_unlink to return 1 (failure) - must be async
    async def mock_handle_unlink(workdir, dry_run):
        return 1

    monkeypatch.setattr("bl.clean_project.handle_unlink", mock_handle_unlink)

    ret = await clean_project(project, unlink=True)
    assert ret == 1


@pytest.mark.asyncio
async def test_clean_project_handles_remove_failure(monkeypatch, tmp_path: Path) -> None:
    """Test that handle_remove failure propagates to clean_project return."""
    from bl.clean_project import clean_project, handle_remove

    workdir = tmp_path
    project = ProjectSpec(repos={}, workdir=workdir)

    # Mock handle_remove to return 1 (failure) - must be async
    async def mock_handle_remove(workdir, force, dry_run):
        return 1

    monkeypatch.setattr("bl.clean_project.handle_remove", mock_handle_remove)

    ret = await clean_project(project, remove=True)
    assert ret == 1


@pytest.mark.asyncio
async def test_clean_project_handles_unlink_failure(monkeypatch, tmp_path: Path) -> None:
    """Test that handle_unlink failure propagates to clean_project return."""
    from bl.clean_project import clean_project, handle_unlink

    workdir = tmp_path
    project = ProjectSpec(repos={}, workdir=workdir)

    # Mock handle_unlink to return 1 (failure) - must be async
    async def mock_handle_unlink(workdir, dry_run):
        return 1

    monkeypatch.setattr("bl.clean_project.handle_unlink", mock_handle_unlink)

    ret = await clean_project(project, unlink=True)
    assert ret == 1


@pytest.mark.asyncio
async def test_clean_project_handles_remove_failure(monkeypatch, tmp_path: Path) -> None:
    """Test that handle_remove failure propagates to clean_project return."""
    from bl.clean_project import clean_project, handle_remove

    workdir = tmp_path
    project = ProjectSpec(repos={}, workdir=workdir)

    # Mock handle_remove to return 1 (failure) - must be async
    async def mock_handle_remove(workdir, force, dry_run):
        return 1

    monkeypatch.setattr("bl.clean_project.handle_remove", mock_handle_remove)

    ret = await clean_project(project, remove=True)
    assert ret == 1
