from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from bl.spec_parser import load_spec_file
from bl.spec_processor import process_project
from bl.types import ProjectSpec
from tests.test_frozen_processor import _init_test_repo, _run_git


@pytest.mark.asyncio
async def test_process_project_minimal_integration(tmp_path: Path) -> None:
    with TemporaryDirectory() as td:
        td_path = Path(td)
        remote_repo, _, head_sha = _init_test_repo(td_path)

        workdir = td_path / "workdir"
        workdir.mkdir()

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

        spec_path = workdir / "spec.yaml"
        spec_path.write_text(yaml.safe_dump(spec_data))

        project = load_spec_file(spec_path, None, workdir)
        assert isinstance(project, ProjectSpec)

        await process_project(project, concurrency=1)

        module_repo = workdir / "external-src" / "test-module"
        assert module_repo.is_dir()
        current_head = _run_git(module_repo, "rev-parse", "HEAD")
        assert current_head == head_sha


@pytest.mark.asyncio
async def test_conflict_reports_applied_refs_and_aborts(monkeypatch, tmp_path: Path) -> None:
    from bl.utils import bl_env
    from tests.conftest import _make_repo_processor

    remote_repo, _, base_sha = _init_test_repo(tmp_path)
    _run_git(remote_repo, "branch", "18.0")
    _run_git(remote_repo, "switch", "-c", "first-pr")
    (remote_repo / "file.txt").write_text("first PR\n")
    _run_git(remote_repo, "commit", "-am", "first PR")
    _run_git(remote_repo, "update-ref", "refs/pull/1125/head", "HEAD")
    _run_git(remote_repo, "switch", "-c", "18.0-fix-the-things")
    (remote_repo / "extra.txt").write_text("extra change\n")
    _run_git(remote_repo, "add", "extra.txt")
    _run_git(remote_repo, "commit", "-m", "extra change")
    applied_sha = _run_git(remote_repo, "rev-parse", "HEAD")
    _run_git(remote_repo, "switch", "-c", "conflicting-pr", base_sha)
    (remote_repo / "file.txt").write_text("conflicting PR\n")
    _run_git(remote_repo, "commit", "-am", "conflicting PR")
    _run_git(remote_repo, "update-ref", "refs/pull/1234/head", "HEAD")
    _run_git(remote_repo, "branch", "later")

    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        yaml.safe_dump(
            {
                "test-module": {
                    "remotes": {"oca": str(remote_repo)},
                    "merges": [
                        "oca 18.0",
                        "oca refs/pull/1125/head",
                        "oca 18.0-fix-the-things",
                        "oca refs/pull/1234/head",
                        "oca later",
                    ],
                }
            }
        )
    )
    repo_info = load_spec_file(spec_path, None, tmp_path).repos["test-module"]
    rp = _make_repo_processor(tmp_path, repo_info)
    statuses = []
    monkeypatch.setattr(rp.progress, "update", lambda *args, **kwargs: statuses.append(kwargs.get("status", "")))
    for key, value in {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():
        monkeypatch.setitem(bl_env, key, value)
    module_path = tmp_path / "checkout"

    ret, _ = await rp.process_repo(module_path, [], [])

    assert ret != 0
    assert "Could not apply oca/#1234 to oca/18.0 + oca/#1125 + oca/18.0-fix-the-things:" in statuses[-1]
    assert "CONFLICT" in statuses[-1]
    assert "oca/later" not in statuses[-1]
    assert _run_git(module_path, "rev-parse", "HEAD") == applied_sha
    assert _run_git(module_path, "status", "--porcelain") == ""
    assert not (module_path / ".git" / "MERGE_HEAD").exists()
