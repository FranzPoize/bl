"""Editing restrictions, shared-source cleanup, and store garbage collection."""

import importlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from bl import config, editable
from bl.clean_project import clean_project
from tests.odoo_worktree_helpers import OdooEnvironment


@pytest.mark.asyncio
async def test_edit_odoo_is_rejected_before_git_or_configuration_changes(
    odoo_store: OdooEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = odoo_store.project("a")
    await project.build()
    before = odoo_store.git(project.source, "rev-parse", "HEAD")
    config_path = config.get_config_file("a")
    config_before = config_path.read_bytes()
    git = AsyncMock(return_value=(0, "", ""))
    hooks = AsyncMock(return_value=(0, "", ""))
    monkeypatch.setattr(editable, "run_git", git)
    monkeypatch.setattr(editable, "remove_locking_pre_commit", hooks)

    with pytest.raises(Exception, match=r"(?i)odoo.*(edit|read.only|managed|shared)"):
        await editable.make_editable("odoo", project.workdir / "spec.yaml", project.workdir)

    git.assert_not_awaited()
    hooks.assert_not_awaited()
    assert config_path.read_bytes() == config_before
    assert odoo_store.git(project.source, "rev-parse", "HEAD") == before


@pytest.mark.asyncio
async def test_editable_true_in_odoo_spec_is_rejected(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    project.configure(editable=True)

    with pytest.raises(Exception, match=r"(?i)odoo.*(edit|read.only|managed|shared)"):
        await project.build()

    assert not project.source.exists()


@pytest.mark.asyncio
async def test_saved_odoo_editable_setting_cannot_bypass_restriction(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    await project.build()
    before = odoo_store.git(project.source, "rev-parse", "HEAD")
    settings = config.load_config("a")
    settings["editable"] = {"odoo": "True"}
    config.write_config("a", settings)
    odoo_store.advance()

    with pytest.raises(Exception, match=r"(?i)odoo.*(edit|read.only|managed|shared)"):
        await project.build()

    assert odoo_store.git(project.source, "rev-parse", "HEAD") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("same_name", [False, True])
async def test_clean_remove_unlinks_only_one_consumer(odoo_store: OdooEnvironment, same_name: bool) -> None:
    a = odoo_store.project("parent-a/same" if same_name else "a")
    b = odoo_store.project("parent-b/same" if same_name else "b")
    await a.build()
    await b.build()
    published_source = a.source.resolve()

    assert await clean_project(a.specification(), remove=True, force=True) == 0

    assert not a.source.exists() and not a.source.is_symlink()
    assert published_source.exists(), "Removing a consumer must retain the published worktree"
    assert (b.source / "odoo/message.txt").read_text() == "original\n"
    await a.build()
    assert a.source.resolve() == b.source.resolve() == published_source


@pytest.mark.asyncio
async def test_clean_dry_run_preserves_links_and_shared_worktree(odoo_store: OdooEnvironment) -> None:
    a, b = odoo_store.project("a"), odoo_store.project("b")
    await a.build()
    await b.build()
    before = [(p.source.resolve(), odoo_store.git(p.source, "rev-parse", "HEAD")) for p in (a, b)]

    assert await clean_project(a.specification(), remove=True, force=True, dry_run=True) == 0

    for project, (source, head) in zip((a, b), before):
        assert project.source.resolve() == source
        assert odoo_store.git(project.source, "rev-parse", "HEAD") == head


@pytest.mark.asyncio
async def test_project_clean_never_resets_shared_source(
    odoo_store: OdooEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = odoo_store.project("a")
    await project.build()
    message = project.source / "odoo/message.txt"
    # Simulate accidental corruption by a user who bypassed read-only permissions.
    message.chmod(message.stat().st_mode | 0o200)
    message.write_text("manual modification\n")
    monkeypatch.setattr("builtins.input", lambda _: "y")

    await clean_project(project.specification())

    assert message.read_text() == "manual modification\n", "Project cleanup must not reset shared files"


async def _prune(root: Path, *, dry_run: bool) -> None:
    """Exercise the store cleanup API while keeping lifecycle assertions local."""
    assert importlib.util.find_spec("bl.odoo_store") is not None, "Shared Odoo store cleanup is not implemented"
    store = importlib.import_module("bl.odoo_store")
    await store.prune_unused_worktrees(root, dry_run=dry_run)


@pytest.mark.asyncio
async def test_store_prune_preserves_live_worktree_then_removes_unused_registration(
    odoo_store: OdooEnvironment,
) -> None:
    a, b = odoo_store.project("a"), odoo_store.project("b")
    await a.build()
    await b.build()
    source = b.source.resolve()
    common = odoo_store.common_dir(b)
    await clean_project(a.specification(), remove=True, force=True)

    await _prune(odoo_store.store_root, dry_run=False)

    assert source.exists()
    assert (b.source / "odoo/message.txt").read_text() == "original\n"
    await clean_project(b.specification(), remove=True, force=True)
    await _prune(odoo_store.store_root, dry_run=False)
    assert not source.exists()
    if common.exists():
        registered = odoo_store.git(common, "worktree", "list", "--porcelain")
        assert f"worktree {source}\n" not in registered + "\n"


@pytest.mark.asyncio
async def test_store_prune_dry_run_preserves_unused_worktree(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    await project.build()
    source = project.source.resolve()
    common = odoo_store.common_dir(project)
    await clean_project(project.specification(), remove=True, force=True)

    await _prune(odoo_store.store_root, dry_run=True)

    assert source.exists()
    assert f"worktree {source}\n" in odoo_store.git(common, "worktree", "list", "--porcelain") + "\n"
