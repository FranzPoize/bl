"""Cross-remote, freeze, failure, and compatibility contracts for shared builds."""

import pytest
import yaml

from bl.freezer import freeze_project
from bl.odoo_store import OdooStoreError
from tests.odoo_worktree_helpers import OdooEnvironment


@pytest.mark.asyncio
async def test_patch_glob_is_sorted_and_legacy_git_am_reuses_same_worktree(odoo_store: OdooEnvironment) -> None:
    patches = odoo_store.patch_series("first", "second")
    a, b = odoo_store.project("a"), odoo_store.project("b", patches)
    directory = a.workdir / "patches" / "ordered"
    directory.mkdir()
    (directory / "02.patch").write_text(patches[1])
    (directory / "01.patch").write_text(patches[0])
    a.configure(patch_globs=["../patches/ordered/*.patch"])
    b.configure(patch_globs=[], shell_command_after=["git am ../patches/fix-*.patch"])

    await a.build()
    await b.build()

    assert (a.source / "odoo/message.txt").read_text() == "second\n"
    assert a.source.resolve() == b.source.resolve()


@pytest.mark.asyncio
async def test_missing_patch_preserves_current_consumers(odoo_store: OdooEnvironment) -> None:
    a, b = odoo_store.project("a"), odoo_store.project("b")
    await a.build()
    await b.build()
    source = a.source.resolve()
    head = odoo_store.git(a.source, "rev-parse", "HEAD")
    a.configure(patch_globs=["../patches/missing*.patch"])

    with pytest.raises(OdooStoreError, match="no matches"):
        await a.build()

    assert a.source.resolve() == b.source.resolve() == source
    assert odoo_store.git(b.source, "rev-parse", "HEAD") == head


@pytest.mark.asyncio
async def test_cross_remote_merge_freezes_each_input_and_rebuilds_pinned_patch(odoo_store: OdooEnvironment) -> None:
    base = odoo_store.git(odoo_store.remote, "rev-parse", "18.0")
    patches = odoo_store.patch_series("patched")
    fork = odoo_store.root / "fork"
    odoo_store.git(odoo_store.root, "clone", odoo_store.remote.as_uri(), str(fork))
    (fork / "odoo/fork.txt").write_text("fork addition\n")
    odoo_store.git(fork, "add", "odoo/fork.txt")
    odoo_store.git(fork, "commit", "-m", "Fork change")
    fork_sha = odoo_store.git(fork, "rev-parse", "HEAD")
    a, b = odoo_store.project("a", patches), odoo_store.project("b", patches)
    # Start shallow, then upgrade to merge-capable history in the same store.
    a.configure(patch_globs=[])
    await a.build()
    for project in (a, b):
        project.configure(
            src="",
            remotes={"origin": odoo_store.remote.as_uri(), "vendor": fork.as_uri()},
            merges=["origin 18.0", "vendor 18.0"],
            patch_globs=["../patches/fix-0.patch"],
        )
        await project.build()
    expected = {"odoo": {"origin": {"18.0": base}, "vendor": {"18.0": fork_sha}}}

    await freeze_project(a.specification(), a.workdir / "frozen.yaml", concurrency=1)

    assert yaml.safe_load((a.workdir / "frozen.yaml").read_text()) == expected
    assert odoo_store.git(a.source, "rev-parse", "HEAD") not in (base, fork_sha)
    odoo_store.advance()
    await b.build()
    await a.build()  # A now follows its frozen input commits.
    assert (a.source / "odoo/version.txt").read_text() == "old upstream\n"
    assert (b.source / "odoo/version.txt").read_text() == "new upstream\n"
    assert (a.source / "odoo/message.txt").read_text() == "patched\n"
    assert (a.source / "odoo/fork.txt").read_text() == "fork addition\n"
    assert odoo_store.common_dir(a) == odoo_store.common_dir(b)


@pytest.mark.asyncio
async def test_pr_merge_and_conflict_leave_published_source_intact(odoo_store: OdooEnvironment) -> None:
    odoo_store.git(odoo_store.remote, "switch", "-c", "fix", "18.0")
    (odoo_store.remote / "odoo/message.txt").write_text("fix\n")
    odoo_store.git(odoo_store.remote, "commit", "-am", "Fix message")
    odoo_store.git(odoo_store.remote, "update-ref", "refs/pull/1/head", "HEAD")
    odoo_store.git(odoo_store.remote, "switch", "18.0")
    a, b = odoo_store.project("a"), odoo_store.project("b")
    for project in (a, b):
        project.configure(merges=["origin refs/pull/1/head"])
        await project.build()
    before = odoo_store.git(a.source, "rev-parse", "HEAD")
    source = a.source.resolve()
    odoo_store.advance(conflict=True)

    with pytest.raises(OdooStoreError):
        await a.build()

    assert a.source.resolve() == b.source.resolve() == source
    for project in (a, b):
        assert odoo_store.git(project.source, "rev-parse", "HEAD") == before
        assert (project.source / "odoo/message.txt").read_text() == "fix\n"
        assert odoo_store.git(project.source, "status", "--porcelain") == ""
    assert not list(odoo_store.store_root.glob("worktrees/*/.preparing-*"))


@pytest.mark.asyncio
async def test_patches_can_touch_files_outside_final_sparse_selection(odoo_store: OdooEnvironment) -> None:
    odoo_store.git(odoo_store.remote, "switch", "--detach", "18.0")
    (odoo_store.remote / "addons/sale/__manifest__.py").write_text("{'name': 'Patched sale'}\n")
    odoo_store.git(odoo_store.remote, "commit", "-am", "Patch excluded module")
    patch = odoo_store.git(odoo_store.remote, "format-patch", "-1", "--stdout") + "\n"
    odoo_store.git(odoo_store.remote, "switch", "18.0")
    project = odoo_store.project("a", [patch])
    project.configure(modules=["account"], locales=["fr"])

    await project.build()

    assert not (project.source / "addons/sale").exists()
    assert (project.source / "addons/account/i18n/fr.po").exists()
    assert odoo_store.git(project.source, "show", "HEAD:addons/sale/__manifest__.py") == "{'name': 'Patched sale'}"


@pytest.mark.asyncio
async def test_basic_identity_survives_upgrade_from_first_store_schema(odoo_store: OdooEnvironment) -> None:
    from bl.odoo_types import content_id

    project = odoo_store.project("a")
    await project.build()
    record_path = next((odoo_store.store_root / "metadata").glob("*.json"))
    import json

    record = json.loads(record_path.read_text())
    recipe = record["recipe"]
    for field in ("merges", "patch_digests", "ref_kind"):
        recipe.pop(field)
    record.pop("resolved_refs")
    assert content_id(recipe) == project.source.resolve().name
    record_path.write_text(json.dumps(record))
    original = project.source.resolve()
    odoo_store.advance()

    await project.build()

    assert project.source.resolve() == original
    assert (project.source / "odoo/version.txt").read_text() == "new upstream\n"
