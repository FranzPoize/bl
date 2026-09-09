"""Project-local Odoo builds opt out of shared storage."""

import pytest

from bl.odoo_store import OdooStoreError, prune_unused_worktrees
from bl.spec_processor import process_project
from tests.odoo_worktree_helpers import OdooEnvironment


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["src", "custom-source"])
async def test_local_clone_and_rebuild(odoo_store: OdooEnvironment, target: str) -> None:
    project = odoo_store.project("a")
    project.configure(target_folder=target, modules=["account"], locales=["fr"])

    await process_project(project.specification(), concurrency=1, local_odoo=True)

    assert not project.source.is_symlink()
    assert (project.source / ".git").is_dir()
    assert odoo_store.common_dir(project) == project.source / ".git"
    assert not odoo_store.store_root.exists()
    assert (project.source / "addons/account/i18n/fr.po").is_file()
    assert not (project.source / "addons/account/i18n/es.po").exists()
    assert not (project.source / "addons/sale").exists()

    upstream = odoo_store.advance()
    await process_project(project.specification(), concurrency=1, local_odoo=True)

    assert odoo_store.git(project.source, "rev-parse", "HEAD") == upstream
    assert not project.source.is_symlink()
    assert not list(project.workdir.glob(f"{target}.bl-backup-*"))


@pytest.mark.asyncio
async def test_switch_to_local_does_not_change_shared_consumer(odoo_store: OdooEnvironment) -> None:
    patches = odoo_store.patch_series("private fix")
    local, shared = odoo_store.project("a", patches), odoo_store.project("b")
    local.configure(patch_globs=[])
    await local.build()
    await shared.build()
    published = shared.source.resolve()
    head = odoo_store.git(published, "rev-parse", "HEAD")
    local.configure(patch_globs=["../patches/fix-0.patch"])

    await process_project(local.specification(), concurrency=1, local_odoo=True)

    assert not local.source.is_symlink()
    assert (local.source / ".git").is_dir()
    assert (local.source / "odoo/message.txt").read_text() == "private fix\n"
    assert shared.source.resolve() == published
    assert odoo_store.git(published, "rev-parse", "HEAD") == head
    assert (published / "odoo/message.txt").read_text() == "original\n"
    assert await prune_unused_worktrees(odoo_store.store_root) == []


@pytest.mark.asyncio
async def test_local_build_runs_shell_commands(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    project.configure(shell_command_after=["echo local > odoo/generated.txt"])

    await process_project(project.specification(), concurrency=1, local_odoo=True)

    assert (project.source / "odoo/generated.txt").read_text() == "local\n"
    assert not odoo_store.store_root.exists()


@pytest.mark.asyncio
async def test_local_build_honors_frozen_revision(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    pinned = odoo_store.git(odoo_store.remote, "rev-parse", "HEAD")
    project.pin(pinned)
    odoo_store.advance()

    await process_project(project.specification(), concurrency=1, local_odoo=True)

    assert odoo_store.git(project.source, "rev-parse", "HEAD") == pinned
    assert not odoo_store.store_root.exists()


@pytest.mark.asyncio
async def test_build_without_flag_migrates_local_clone_back_to_shared(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    await process_project(project.specification(), concurrency=1, local_odoo=True)

    await project.build()

    assert project.source.is_symlink()
    backups = list(project.workdir.glob("src.bl-backup-*"))
    assert len(backups) == 1
    assert (backups[0] / ".git").is_dir()


@pytest.mark.asyncio
async def test_local_build_rejects_unmanaged_symlink(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    project.source.symlink_to(odoo_store.remote, target_is_directory=True)
    before = odoo_store.git(odoo_store.remote, "rev-parse", "HEAD")

    with pytest.raises(OdooStoreError, match="unmanaged symlink"):
        await process_project(project.specification(), concurrency=1, local_odoo=True)

    assert project.source.resolve() == odoo_store.remote
    assert odoo_store.git(odoo_store.remote, "rev-parse", "HEAD") == before
