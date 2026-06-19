from __future__ import annotations

from pathlib import Path

import pytest

from bl import config as bl_config
from bl import editable
from bl.types import OriginType, ProjectSpec, RefspecInfo, RepoInfo


@pytest.mark.asyncio
async def test_remove_locking_pre_commit_removes_bl_precommit_hook(monkeypatch, tmp_path: Path) -> None:
    module_path = tmp_path / "repo"
    hook = module_path / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/sh\n# bl-precommit\n")

    async def fake_run(*args):
        assert args == ("grep", "bl-precommit", str(hook))
        return 0, f"{hook}:# bl-precommit\n", ""

    monkeypatch.setattr(editable, "run", fake_run)

    result = await editable.remove_locking_pre_commit(module_path)

    assert result == (0, f"{hook}:# bl-precommit\n", "")
    assert not hook.exists()


@pytest.mark.asyncio
async def test_make_editable_fetches_full_repo_removes_hook_and_writes_state(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "project" / "odoo"
    module_path = workdir / "external-src" / "test-repo"
    module_path.mkdir(parents=True)
    repo_info = RepoInfo(
        modules=[],
        remotes={"origin": "https://example.com/repo.git"},
        refspecs=[RefspecInfo("origin", "main", OriginType.BRANCH, None)],
        target_folder=None,
    )
    project_spec = ProjectSpec({"test-repo": repo_info}, workdir)
    calls = []

    async def fake_run_git(*args, cwd=None):
        calls.append((args, cwd))
        if args == ("fetch", "--unshallow"):
            return 1, "", "already complete"
        return 0, "", ""

    async def fake_remove_locking_pre_commit(path):
        calls.append((("remove-locking-pre-commit",), path))
        return 0, "", ""

    monkeypatch.setattr(editable, "load_spec_file", lambda spec, frozen, wd, overrides: project_spec)
    monkeypatch.setattr(editable, "run_git", fake_run_git)
    monkeypatch.setattr(editable, "remove_locking_pre_commit", fake_remove_locking_pre_commit)
    monkeypatch.setattr(bl_config, "xdg_config_home", lambda: tmp_path / "xdg")

    await editable.make_editable("test-repo", tmp_path / "spec.yaml", workdir)

    git_args = [args for args, cwd in calls]
    assert git_args[:2] == [("fetch", "--unshallow"), ("fetch", "origin")]
    assert ("sparse-checkout", "disable") in git_args
    assert ("config", "--unset", "extensions.partialClone") in git_args
    assert ("config", "--unset", "promisor") in git_args
    assert ("config", "--unset", "remote.origin.partialclonefilter") in git_args
    assert ("fetch", "--refetch", "origin") in git_args
    assert (("remove-locking-pre-commit",), module_path) in calls

    config = bl_config.load_config("project")
    assert config["editable"]["test-repo"] == "True"
