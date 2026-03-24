from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from bl.freezer import freeze_project, freeze_spec
from bl.types import OriginType, ProjectSpec, RefspecInfo, RepoInfo


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


@pytest.mark.asyncio
async def test_freeze_spec_basic(tmp_path: Path) -> None:
    sem = asyncio.Semaphore(1)
    progress = MagicMock()
    task_id = MagicMock()

    module_name = "test_module"
    module_spec = RepoInfo(
        modules=[],
        remotes={"origin": "https://example.com/test.git"},
        refspecs=[
            RefspecInfo("origin", "main", OriginType.BRANCH, None),
        ],
        shell_commands=[],
        patch_globs_to_apply=[],
        target_folder=None,
        locales=[],
    )

    workdir = tmp_path

    with patch("bl.freezer.run_git", new_callable=AsyncMock) as mock_run_git:
        mock_run_git.return_value = (0, "abc123def456", "")

        result = await freeze_spec(sem, progress, task_id, module_name, module_spec, workdir)

        assert module_name in result
        progress.advance.assert_called_once()


@pytest.mark.asyncio
async def test_freeze_spec_multiple_refspecs(tmp_path: Path) -> None:
    sem = asyncio.Semaphore(1)
    progress = MagicMock()
    task_id = MagicMock()

    module_name = "test_module"
    module_spec = RepoInfo(
        modules=[],
        remotes={"origin": "https://example.com/test.git"},
        refspecs=[
            RefspecInfo("origin", "main", OriginType.BRANCH, None),
            RefspecInfo("origin", "develop", OriginType.BRANCH, None),
        ],
        shell_commands=[],
        patch_globs_to_apply=[],
        target_folder=None,
        locales=[],
    )

    workdir = tmp_path

    with patch("bl.freezer.run_git", new_callable=AsyncMock) as mock_run_git:
        mock_run_git.return_value = (0, "abc123", "")

        result = await freeze_spec(sem, progress, task_id, module_name, module_spec, workdir)

        assert module_name in result
        assert progress.advance.call_count == 1


@pytest.mark.asyncio
async def test_freeze_project_writes_yaml(tmp_path: Path) -> None:
    workdir = tmp_path

    repo_info = RepoInfo(
        modules=[],
        remotes={"origin": "https://example.com/test.git"},
        refspecs=[
            RefspecInfo("origin", "main", OriginType.BRANCH, None),
        ],
        shell_commands=[],
        patch_globs_to_apply=[],
        target_folder=None,
        locales=[],
    )

    project_spec = ProjectSpec(repos={"test_module": repo_info}, workdir=workdir)
    freeze_file = tmp_path / "frozen.yaml"

    with patch("bl.freezer.run_git", new_callable=AsyncMock) as mock_run_git:
        mock_run_git.return_value = (0, "abc123def456", "")

        result = await freeze_project(project_spec, freeze_file, concurrency=1)

        assert result == 0
        assert freeze_file.exists()

        with open(freeze_file) as f:
            data = yaml.safe_load(f)
            assert "test_module" in data
            assert "origin" in data["test_module"]
            assert "main" in data["test_module"]["origin"]


@pytest.mark.asyncio
async def test_freeze_project_default_filename(tmp_path: Path) -> None:
    workdir = tmp_path

    repo_info = RepoInfo(
        modules=[],
        remotes={"origin": "https://example.com/test.git"},
        refspecs=[
            RefspecInfo("origin", "main", OriginType.BRANCH, None),
        ],
        shell_commands=[],
        patch_globs_to_apply=[],
        target_folder=None,
        locales=[],
    )

    project_spec = ProjectSpec(repos={"test_module": repo_info}, workdir=workdir)

    with patch("bl.freezer.run_git", new_callable=AsyncMock) as mock_run_git:
        mock_run_git.return_value = (0, "abc123", "")

        result = await freeze_project(project_spec, False, concurrency=1)

        assert result == 0
        frozen_file = workdir / "frozen.yaml"
        assert frozen_file.exists()


@pytest.mark.asyncio
async def test_freeze_spec_with_ref_name(tmp_path: Path) -> None:
    sem = asyncio.Semaphore(1)
    progress = MagicMock()
    task_id = MagicMock()

    module_name = "test_module"
    module_spec = RepoInfo(
        modules=[],
        remotes={"origin": "https://example.com/test.git"},
        refspecs=[
            RefspecInfo("origin", "abc123def456", OriginType.REF, "my-ref"),
        ],
        shell_commands=[],
        patch_globs_to_apply=[],
        target_folder=None,
        locales=[],
    )

    workdir = tmp_path

    with patch("bl.freezer.run_git", new_callable=AsyncMock) as mock_run_git:
        mock_run_git.return_value = (0, "abc123def456", "")

        result = await freeze_spec(sem, progress, task_id, module_name, module_spec, workdir)

        assert module_name in result
        assert "my-ref" in result[module_name]["origin"]
