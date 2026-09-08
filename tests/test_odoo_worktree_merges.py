"""Sharing includes ordered merges and tracks updates to their source branches."""

import pytest

from tests.odoo_worktree_helpers import OdooEnvironment, OdooProject


def _add_fix_branch(store: OdooEnvironment, branch: str) -> str:
    store.git(store.remote, "switch", "-c", branch, "18.0")
    (store.remote / "odoo" / f"{branch}.txt").write_text(f"{branch}\n")
    store.git(store.remote, "add", "odoo")
    store.git(store.remote, "commit", "-m", branch)
    sha = store.git(store.remote, "rev-parse", "HEAD")
    store.git(store.remote, "switch", "18.0")
    return sha


def _configure_merges(store: OdooEnvironment, project: OdooProject, *branches: str) -> None:
    project.configure(
        src="", remotes={"origin": store.remote.as_uri()}, merges=[f"origin {ref}" for ref in ("18.0", *branches)]
    )


@pytest.mark.asyncio
async def test_identical_merge_sequences_share_worktree(odoo_store: OdooEnvironment) -> None:
    _add_fix_branch(odoo_store, "fix-a")
    _add_fix_branch(odoo_store, "fix-b")
    a, b = odoo_store.project("a"), odoo_store.project("b")
    for project in (a, b):
        _configure_merges(odoo_store, project, "fix-a", "fix-b")
        await project.build()
        assert (project.source / "odoo/fix-a.txt").read_text() == "fix-a\n"
        assert (project.source / "odoo/fix-b.txt").read_text() == "fix-b\n"

    assert a.source.resolve() == b.source.resolve()


@pytest.mark.asyncio
async def test_reversing_merge_order_uses_distinct_worktrees_with_shared_storage(odoo_store: OdooEnvironment) -> None:
    fix_a = _add_fix_branch(odoo_store, "fix-a")
    fix_b = _add_fix_branch(odoo_store, "fix-b")
    a, b = odoo_store.project("a"), odoo_store.project("b")
    _configure_merges(odoo_store, a, "fix-a", "fix-b")
    _configure_merges(odoo_store, b, "fix-b", "fix-a")

    await a.build()
    await b.build()

    # Equal files do not imply equal merge recipes or merge histories.
    assert odoo_store.git(a.source, "rev-parse", "HEAD^{tree}") == odoo_store.git(b.source, "rev-parse", "HEAD^{tree}")
    assert odoo_store.git(a.source, "rev-parse", "HEAD^2") == fix_b
    assert odoo_store.git(b.source, "rev-parse", "HEAD^2") == fix_a
    assert a.source.resolve() != b.source.resolve()
    assert odoo_store.common_dir(a) == odoo_store.common_dir(b)


@pytest.mark.asyncio
async def test_merged_branch_update_is_visible_to_other_consumer(odoo_store: OdooEnvironment) -> None:
    _add_fix_branch(odoo_store, "fix-a")
    a, b = odoo_store.project("a"), odoo_store.project("b")
    for project in (a, b):
        _configure_merges(odoo_store, project, "fix-a")
        await project.build()
    odoo_store.git(odoo_store.remote, "switch", "fix-a")
    (odoo_store.remote / "odoo/fix-a.txt").write_text("updated fix\n")
    odoo_store.git(odoo_store.remote, "commit", "-am", "Update fix")
    latest = odoo_store.git(odoo_store.remote, "rev-parse", "HEAD")
    odoo_store.git(odoo_store.remote, "switch", "18.0")

    await a.build()

    for project in (a, b):
        assert (project.source / "odoo/fix-a.txt").read_text() == "updated fix\n"
        odoo_store.git(project.source, "merge-base", "--is-ancestor", latest, "HEAD")
    assert a.source.resolve() == b.source.resolve()
