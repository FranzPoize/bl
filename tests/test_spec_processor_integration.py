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
async def test_process_project_src_remote_becomes_named(tmp_path: Path) -> None:
    """An existing src checkout can fetch after its sole remote is explicitly named."""
    remote_repo, _, initial_sha = _init_test_repo(tmp_path)
    remote_url = remote_repo.as_uri()
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    spec_path = workdir / "spec.yaml"
    spec_path.write_text(yaml.safe_dump({"test-module": {"src": f"{remote_url} main", "modules": []}}))

    project = load_spec_file(spec_path, None, workdir)
    await process_project(project, concurrency=1)

    module_repo = workdir / "external-src" / "test-module"
    assert _run_git(module_repo, "remote") == "origin"
    assert _run_git(module_repo, "remote", "get-url", "origin") == remote_url
    assert _run_git(module_repo, "rev-parse", "HEAD") == initial_sha
    assert _run_git(module_repo, "rev-parse", "--is-shallow-repository") == "true"

    # Advance the same upstream so that retaining the old checkout cannot pass.
    (remote_repo / "file.txt").write_text("third\n")
    _run_git(remote_repo, "add", "file.txt")
    _run_git(remote_repo, "commit", "-m", "third")
    updated_sha = _run_git(remote_repo, "rev-parse", "HEAD")

    spec_path.write_text(
        yaml.safe_dump(
            {
                "test-module": {
                    "modules": [],
                    "remotes": {"upstream": remote_url},
                    "merges": ["upstream main"],
                }
            }
        )
    )
    project = load_spec_file(spec_path, None, workdir)
    await process_project(project, concurrency=1)

    assert _run_git(module_repo, "remote", "get-url", "upstream") == remote_url
    assert _run_git(module_repo, "rev-parse", "refs/remotes/upstream/main") == updated_sha
    assert _run_git(module_repo, "rev-parse", "HEAD") == updated_sha
    assert (module_repo / "file.txt").read_text() == "third\n"
