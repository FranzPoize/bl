from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from bl.spec_processor import RepoProcessor
from bl.types import OriginType, RefspecInfo, RepoInfo


def _make_ref(remote: str, refspec: str, type_: OriginType = OriginType.BRANCH) -> RefspecInfo:
    return RefspecInfo(remote, refspec, type_, None)


def _make_repo_info(
    modules: List[str] | None = None,
    remotes: dict[str, str] | None = None,
    refspecs: List[RefspecInfo] | None = None,
    shell_commands: List[str] | None = None,
    patch_globs_to_apply: List[str] | None = None,
    locales: List[str] | None = None,
    paths: dict[str, List[str]] | None = None,
) -> RepoInfo:
    return RepoInfo(
        modules=modules or [],
        remotes=remotes or {},
        refspecs=refspecs or [],
        shell_commands=shell_commands or [],
        patch_globs_to_apply=patch_globs_to_apply or [],
        target_folder=None,
        locales=locales or [],
        paths=paths or {},
    )


def _make_repo_processor(tmp_path: Path, repo_info: RepoInfo) -> RepoProcessor:
    class DummyProgress:
        def update(self, *args, **kwargs) -> None:
            return None

        def advance(self, *args, **kwargs) -> None:
            return None

        def add_task(self, *args, **kwargs) -> int:
            return 0

        def remove_task(self, *args, **kwargs) -> None:
            return None

    semaphore = pytest.importorskip("asyncio").Semaphore(1)
    dummy_progress = DummyProgress()
    dummy_count_progress = DummyProgress()
    dummy_task_id = 0
    return RepoProcessor(
        workdir=tmp_path,
        name="test-repo",
        config_file={},
        semaphore=semaphore,
        repo_info=repo_info,
        progress=dummy_progress,
        count_progress=dummy_count_progress,
        count_task=dummy_task_id,
        concurrency=1,
    )
