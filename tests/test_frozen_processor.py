"""Processor-level tests for frozen_sha integration."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from bl.spec_parser import load_spec_file
from bl.spec_processor import process_project


def _run_git(repo: Path, *args: str) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_test_repo(base_dir: Path) -> tuple[Path, str, str]:
    """Create a test git repo with two commits, return repo path and both SHAs."""
    repo = base_dir / "remote_repo"
    repo.mkdir(parents=True, exist_ok=True)

    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.name", "Test User")
    _run_git(repo, "config", "user.email", "test@example.com")

    # First commit
    (repo / "file.txt").write_text("first\n")
    _run_git(repo, "add", "file.txt")
    _run_git(repo, "commit", "-m", "first")
    first_sha = _run_git(repo, "rev-parse", "HEAD")

    # Second commit so that branch tip moves forward
    (repo / "file.txt").write_text("second\n")
    _run_git(repo, "add", "file.txt")
    _run_git(repo, "commit", "-m", "second")
    head_sha = _run_git(repo, "rev-parse", "HEAD")

    return repo, first_sha, head_sha


@pytest.mark.asyncio
async def test_processor_honors_frozen_sha_for_branch() -> None:
    """Test that SpecProcessor checks out frozen SHA instead of branch tip."""
    with TemporaryDirectory() as td:
        td_path = Path(td)
        remote_repo, frozen_sha, branch_head = _init_test_repo(td_path)

        # Sanity: main branch points at latest commit (not the frozen one)
        assert frozen_sha != branch_head

        workdir = td_path / "workdir"
        workdir.mkdir()

        # Build a minimal spec + frozen.yaml that point to our local repo
        spec_data = {
            "test-module": {
                "modules": [],
                "remotes": {
                    "origin": str(remote_repo),
                },
                "merges": [
                    "origin main",
                ],
            }
        }
        frozen_mapping = {
            "test-module": {
                "origin": {
                    "main": frozen_sha,
                }
            }
        }

        spec_path = workdir / "spec.yaml"
        frozen_path = workdir / "frozen.yaml"
        spec_path.write_text(yaml.safe_dump(spec_data))
        frozen_path.write_text(yaml.safe_dump(frozen_mapping))

        project = load_spec_file(spec_path, frozen_path, workdir)
        assert project is not None

        await process_project(project, concurrency=1)

        # After processing, the checked-out repository should be at the frozen SHA
        module_repo = workdir / "external-src" / "test-module"
        assert module_repo.is_dir()
        current_head = _run_git(module_repo, "rev-parse", "HEAD")
        assert current_head == frozen_sha, f"Expected HEAD {frozen_sha}, got {current_head}"
