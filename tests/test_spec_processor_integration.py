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
