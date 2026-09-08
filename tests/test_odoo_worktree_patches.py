"""Acceptance tests for patches on shared, moving Odoo branch worktrees.

Sharing is not implemented yet: failures at the sharing/update assertions are
intentional test-first specifications, not skipped or expected-failure tests.
All Git operations use temporary local repositories and isolated configuration.
"""

from __future__ import annotations

import pytest

from tests.odoo_worktree_helpers import OdooEnvironment


@pytest.mark.asyncio
async def test_odoo_applies_project_relative_patches_in_declared_order(odoo_patches: OdooEnvironment) -> None:
    project = odoo_patches.project("a", odoo_patches.patch_series("first patch", "second patch"))

    await project.build()

    assert (project.source / "odoo/message.txt").read_text() == "second patch\n"
    assert odoo_patches.git(project.source, "status", "--porcelain") == ""


@pytest.mark.asyncio
async def test_identical_patch_contents_share_despite_different_paths(odoo_patches: OdooEnvironment) -> None:
    patches = odoo_patches.patch_series("shared fix")
    a = odoo_patches.project("a", patches, prefix="customer-a")
    b = odoo_patches.project("b", patches, prefix="different-name")

    await a.build()
    await b.build()

    for project in (a, b):
        assert (project.source / "odoo/message.txt").read_text() == "shared fix\n"
    assert a.source.resolve() == b.source.resolve(), "Identical patch contents must reuse one worktree"
    assert (a.source / ".git").is_file(), "The shared checkout must be a linked Git worktree"


@pytest.mark.asyncio
async def test_branch_update_reapplies_patch_for_both_consumers(odoo_patches: OdooEnvironment) -> None:
    patches = odoo_patches.patch_series("shared fix")
    a = odoo_patches.project("a", patches)
    b = odoo_patches.project("b", patches)
    await a.build()
    await b.build()
    upstream_sha = odoo_patches.advance()

    await a.build()  # B is deliberately not rebuilt.

    for project in (a, b):
        assert (project.source / "odoo/version.txt").read_text() == "new upstream\n"
        assert (project.source / "odoo/message.txt").read_text() == "shared fix\n"
        odoo_patches.git(project.source, "merge-base", "--is-ancestor", upstream_sha, "HEAD")
        assert odoo_patches.git(project.source, "rev-list", "--count", f"{upstream_sha}..HEAD") == "1"
    assert a.source.resolve() == b.source.resolve()


@pytest.mark.asyncio
async def test_different_patches_and_unpatched_odoo_share_only_git_storage(odoo_patches: OdooEnvironment) -> None:
    a = odoo_patches.project("a", odoo_patches.patch_series("fix A"))
    b = odoo_patches.project("b", odoo_patches.patch_series("fix B"))
    plain = odoo_patches.project("plain", [])

    for project in (a, b, plain):
        await project.build()

    assert (a.source / "odoo/message.txt").read_text() == "fix A\n"
    assert (b.source / "odoo/message.txt").read_text() == "fix B\n"
    assert (plain.source / "odoo/message.txt").read_text() == "original\n"
    assert len({project.source.resolve() for project in (a, b, plain)}) == 3
    assert len({odoo_patches.common_dir(project) for project in (a, b, plain)}) == 1


@pytest.mark.asyncio
async def test_changing_patch_contents_moves_only_that_project(odoo_patches: OdooEnvironment) -> None:
    patches = odoo_patches.patch_series("old fix")
    replacement = odoo_patches.patch_series("new fix")[0]
    a = odoo_patches.project("a", patches)
    b = odoo_patches.project("b", patches)
    await a.build()
    await b.build()
    previous_b_source = b.source.resolve()
    previous_b_head = odoo_patches.git(b.source, "rev-parse", "HEAD")
    (a.workdir / "patches/fix-0.patch").write_text(replacement)

    await a.build()

    assert (a.source / "odoo/message.txt").read_text() == "new fix\n"
    assert (b.source / "odoo/message.txt").read_text() == "old fix\n"
    assert b.source.resolve() == previous_b_source
    assert odoo_patches.git(b.source, "rev-parse", "HEAD") == previous_b_head
    assert a.source.resolve() != b.source.resolve()
    assert odoo_patches.common_dir(a) == odoo_patches.common_dir(b)


@pytest.mark.asyncio
async def test_rebuilding_patched_odoo_does_not_duplicate_patches(odoo_patches: OdooEnvironment) -> None:
    base_sha = odoo_patches.git(odoo_patches.remote, "rev-parse", "18.0")
    a = odoo_patches.project("a", odoo_patches.patch_series("fix"))
    await a.build()
    original_source = a.source.resolve()
    original_tree = odoo_patches.git(a.source, "rev-parse", "HEAD^{tree}")

    await a.build()

    assert a.source.resolve() == original_source
    assert odoo_patches.git(a.source, "rev-parse", "HEAD^{tree}") == original_tree
    assert odoo_patches.git(a.source, "rev-list", "--count", f"{base_sha}..HEAD") == "1"
    assert odoo_patches.git(a.source, "status", "--porcelain") == ""


@pytest.mark.asyncio
async def test_patch_conflict_preserves_published_source_for_both_projects(odoo_patches: OdooEnvironment) -> None:
    patches = odoo_patches.patch_series("shared fix")
    a = odoo_patches.project("a", patches)
    b = odoo_patches.project("b", patches)
    await a.build()
    await b.build()
    before = [(project.source.resolve(), odoo_patches.git(project.source, "rev-parse", "HEAD")) for project in (a, b)]
    odoo_patches.advance(conflict=True)

    # The public build entry point currently reports repository failures with
    # Exception; tighten this to a domain exception when that API exists.
    with pytest.raises(Exception):
        await a.build()

    for project, (source, head) in zip((a, b), before):
        assert project.source.resolve() == source
        assert odoo_patches.git(project.source, "rev-parse", "HEAD") == head
        assert (project.source / "odoo/message.txt").read_text() == "shared fix\n"
        assert (project.source / "odoo/version.txt").read_text() == "old upstream\n"
        assert odoo_patches.git(project.source, "status", "--porcelain") == ""
