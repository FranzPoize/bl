"""Integration boundaries for shared Odoo specifications and local project data."""

import pytest

from bl.clean_project import clean_project
from bl.odoo_store import OdooStoreError
from tests.odoo_worktree_helpers import OdooEnvironment


@pytest.mark.asyncio
async def test_store_location_can_be_overridden(odoo_store: OdooEnvironment, monkeypatch: pytest.MonkeyPatch) -> None:
    location = odoo_store.root / "another-drive" / "bl-odoo"
    monkeypatch.setenv("BL_ODOO_STORE", str(location))
    project = odoo_store.project("a")

    await project.build()

    assert project.source.resolve().is_relative_to(location)
    assert not odoo_store.store_root.exists()


@pytest.mark.asyncio
async def test_custom_shared_target_is_unlinked_by_clean(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    project.configure(target_folder="custom-source")
    await project.build()
    published = project.source.resolve()

    assert await clean_project(project.specification(), remove=True, force=True) == 0

    assert not project.source.is_symlink()
    assert published.exists()


@pytest.mark.asyncio
async def test_target_cannot_replace_project_itself(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    project.configure(target_folder=".")

    with pytest.raises(OdooStoreError, match="project directory"):
        await project.build()

    assert (project.workdir / "spec.yaml").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("transform", ["patch", "shell", "merge"])
async def test_changed_spec_does_not_transform_other_consumers_source(
    odoo_store: OdooEnvironment, transform: str
) -> None:
    patches = odoo_store.patch_series("private fix")
    a, b = odoo_store.project("a", patches), odoo_store.project("b")
    a.configure(patch_globs=[])
    await a.build()
    await b.build()
    before = odoo_store.git(b.source, "rev-parse", "HEAD")
    if transform == "patch":
        a.configure(patch_globs=["../patches/fix-0.patch"])
    elif transform == "shell":
        a.configure(shell_command_after=["echo modified > odoo/message.txt"])
    else:
        a.configure(merges=["origin 18.0"])

    if transform == "shell":
        with pytest.raises(OdooStoreError, match="shell commands"):
            await a.build()
        assert a.source.resolve() == b.source.resolve()
    else:
        await a.build()
        assert a.source.resolve() != b.source.resolve()
        expected = "private fix\n" if transform == "patch" else "original\n"
        assert (a.source / "odoo/message.txt").read_text() == expected

    assert odoo_store.git(b.source, "rev-parse", "HEAD") == before
    assert (b.source / "odoo/message.txt").read_text() == "original\n"


@pytest.mark.asyncio
async def test_clean_then_build_patched_spec_uses_distinct_shared_checkout(odoo_store: OdooEnvironment) -> None:
    patches = odoo_store.patch_series("private fix")
    a, b = odoo_store.project("a", patches), odoo_store.project("b")
    a.configure(patch_globs=[])
    await a.build()
    await b.build()
    a.configure(patch_globs=["../patches/fix-0.patch"])

    await clean_project(a.specification(), remove=True, force=True)
    await a.build()

    assert a.source.is_symlink()
    assert a.source.resolve() != b.source.resolve()
    assert odoo_store.common_dir(a) == odoo_store.common_dir(b)
    assert (a.source / "odoo/message.txt").read_text() == "private fix\n"
    assert (b.source / "odoo/message.txt").read_text() == "original\n"


@pytest.mark.asyncio
async def test_migration_keeps_local_history_and_ignored_files(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    odoo_store.git(project.workdir, "clone", odoo_store.remote.as_uri(), str(project.source))
    (project.source / "odoo/local.txt").write_text("local commit\n")
    (project.source / ".gitignore").write_text("ignored.txt\n")
    odoo_store.git(project.source, "add", ".")
    odoo_store.git(project.source, "commit", "-m", "Unpushed local work")
    local_sha = odoo_store.git(project.source, "rev-parse", "HEAD")
    (project.source / "ignored.txt").write_text("keep this too\n")

    await project.build()

    backups = list(project.workdir.glob("src.bl-backup-*"))
    assert len(backups) == 1
    assert odoo_store.git(backups[0], "rev-parse", "HEAD") == local_sha
    assert (backups[0] / "ignored.txt").read_text() == "keep this too\n"
    assert (backups[0] / "odoo/local.txt").read_text() == "local commit\n"
    assert not (project.source / "odoo/local.txt").exists()


@pytest.mark.asyncio
async def test_migration_does_not_break_an_existing_linked_worktree(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    odoo_store.git(odoo_store.remote, "worktree", "add", "--detach", str(project.source), "18.0")
    before = odoo_store.git(odoo_store.remote, "worktree", "list", "--porcelain")

    with pytest.raises(OdooStoreError, match="already a linked worktree"):
        await project.build()

    assert odoo_store.git(odoo_store.remote, "worktree", "list", "--porcelain") == before
    assert odoo_store.common_dir(project) == odoo_store.remote / ".git"
    assert (project.source / "odoo/message.txt").read_text() == "original\n"
