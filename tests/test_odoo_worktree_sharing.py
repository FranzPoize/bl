"""Test-first contracts for shared moving branches and frozen Odoo revisions."""

import pytest
import yaml

from bl.freezer import freeze_project
from tests.odoo_worktree_helpers import OdooEnvironment


@pytest.mark.asyncio
async def test_same_branch_reuses_one_detached_worktree_in_user_store(odoo_store: OdooEnvironment) -> None:
    a, b = odoo_store.project("a"), odoo_store.project("b")
    await a.build()
    await b.build()

    assert a.source.is_symlink() and b.source.is_symlink()
    assert a.source.resolve() == b.source.resolve()
    assert a.source.resolve().is_relative_to(odoo_store.store_root)
    assert (a.source / ".git").is_file()
    assert odoo_store.git(a.source, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"


@pytest.mark.asyncio
async def test_unchanged_rebuild_reuses_worktree_without_duplicate_registration(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    await project.build()
    before = (project.source.resolve(), odoo_store.git(project.source, "worktree", "list", "--porcelain"))

    await project.build()

    assert (project.source.resolve(), odoo_store.git(project.source, "worktree", "list", "--porcelain")) == before


@pytest.mark.asyncio
async def test_updating_branch_through_a_updates_b_without_rebuilding_b(odoo_store: OdooEnvironment) -> None:
    a, b = odoo_store.project("a"), odoo_store.project("b")
    await a.build()
    await b.build()
    latest = odoo_store.advance()

    await a.build()

    for project in (a, b):
        assert odoo_store.git(project.source, "rev-parse", "HEAD") == latest
        assert (project.source / "odoo/version.txt").read_text() == "new upstream\n"
    shared_source = a.source.resolve()
    await b.build()
    assert a.source.resolve() == b.source.resolve() == shared_source


@pytest.mark.asyncio
async def test_versions_share_git_storage_but_update_independently(odoo_store: OdooEnvironment) -> None:
    old = odoo_store.git(odoo_store.remote, "rev-parse", "18.0")
    odoo_store.git(odoo_store.remote, "branch", "17.0", old)
    a, b = odoo_store.project("a"), odoo_store.project("b")
    b.configure(src=f"{odoo_store.remote.as_uri()} 17.0")
    await a.build()
    await b.build()
    latest = odoo_store.advance()

    await a.build()

    assert odoo_store.git(a.source, "rev-parse", "HEAD") == latest
    assert odoo_store.git(b.source, "rev-parse", "HEAD") == old
    assert (b.source / "odoo/version.txt").read_text() == "old upstream\n"
    assert a.source.resolve() != b.source.resolve()
    assert odoo_store.common_dir(a) == odoo_store.common_dir(b)


@pytest.mark.asyncio
async def test_same_branch_name_on_different_sources_is_not_shared(odoo_store: OdooEnvironment) -> None:
    fork = odoo_store.root / "fork"
    odoo_store.git(odoo_store.root, "clone", str(odoo_store.remote), str(fork))
    (fork / "odoo/message.txt").write_text("fork content\n")
    odoo_store.git(fork, "commit", "-am", "Fork-specific content")
    a, b = odoo_store.project("a"), odoo_store.project("b")
    b.configure(src=f"{fork.as_uri()} 18.0")

    await a.build()
    await b.build()

    assert (a.source / "odoo/message.txt").read_text() == "original\n"
    assert (b.source / "odoo/message.txt").read_text() == "fork content\n"
    assert a.source.resolve() != b.source.resolve()
    assert odoo_store.common_dir(a) != odoo_store.common_dir(b)


@pytest.mark.asyncio
async def test_remote_aliases_and_equal_project_names_do_not_prevent_sharing(odoo_store: OdooEnvironment) -> None:
    a = odoo_store.project("parent-a/same-name")
    b = odoo_store.project("parent-b/same-name")
    b.configure(src="", remotes={"upstream": odoo_store.remote.as_uri()}, merges=["upstream 18.0"])

    await a.build()
    await b.build()

    assert a.source.resolve() == b.source.resolve()


@pytest.mark.asyncio
async def test_custom_target_folder_attaches_to_same_shared_worktree(odoo_store: OdooEnvironment) -> None:
    a, b = odoo_store.project("a"), odoo_store.project("b")
    b.configure(target_folder="odoo-source")

    await a.build()
    await b.build()

    assert b.source == b.workdir / "odoo-source"
    assert a.source.resolve() == b.source.resolve()
    assert not (b.workdir / "src").exists()


@pytest.mark.asyncio
async def test_reordered_duplicate_module_and_locale_selections_reuse_worktree(odoo_store: OdooEnvironment) -> None:
    a, b = odoo_store.project("a"), odoo_store.project("b")
    a.configure(modules=["account", "sale"], locales=["fr", "es"])
    b.configure(modules=["sale", "account", "sale"], locales=["es", "fr", "fr"])

    await a.build()
    await b.build()

    for module in ("account", "sale"):
        for locale in ("fr", "es"):
            assert (a.source / "addons" / module / "i18n" / f"{locale}.po").is_file()
    assert a.source.resolve() == b.source.resolve()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection", [{"modules": ["sale"], "locales": ["fr"]}, {"modules": ["account"], "locales": ["es"]}]
)
async def test_different_sparse_selections_expand_the_same_source(odoo_store: OdooEnvironment, selection: dict) -> None:
    a, b = odoo_store.project("a"), odoo_store.project("b")
    a.configure(modules=["account"], locales=["fr"])
    b.configure(**selection)
    await a.build()
    before = {str(p.relative_to(a.source)): p.read_bytes() for p in (a.source / "addons").rglob("*") if p.is_file()}

    await b.build()

    after = {str(p.relative_to(a.source)): p.read_bytes() for p in (a.source / "addons").rglob("*") if p.is_file()}
    assert before.items() <= after.items()
    assert (b.source / "addons" / selection["modules"][0] / "i18n" / f"{selection['locales'][0]}.po").is_file()
    assert a.source.resolve() == b.source.resolve()
    assert odoo_store.common_dir(a) == odoo_store.common_dir(b)


@pytest.mark.asyncio
async def test_frozen_consumers_share_exact_revision_and_ignore_branch_updates(odoo_store: OdooEnvironment) -> None:
    old = odoo_store.git(odoo_store.remote, "rev-parse", "18.0")
    a, b, c = (odoo_store.project(name) for name in ("moving", "frozen-b", "frozen-c"))
    b.pin(old)
    c.pin(old)
    for project in (a, b, c):
        await project.build()
    latest = odoo_store.advance()

    await a.build()
    await b.build()

    assert odoo_store.git(a.source, "rev-parse", "HEAD") == latest
    for project in (b, c):
        assert odoo_store.git(project.source, "rev-parse", "HEAD") == old
        assert (project.source / "odoo/version.txt").read_text() == "old upstream\n"
    assert b.source.resolve() == c.source.resolve() != a.source.resolve()
    assert len({odoo_store.common_dir(project) for project in (a, b, c)}) == 1


@pytest.mark.asyncio
async def test_freeze_uses_current_shared_revision_and_each_consumers_alias(odoo_store: OdooEnvironment) -> None:
    a, b = odoo_store.project("a"), odoo_store.project("b")
    b.configure(src="", remotes={"upstream": odoo_store.remote.as_uri()}, merges=["upstream 18.0"])
    await a.build()
    await b.build()
    latest = odoo_store.advance()
    await a.build()

    for project, alias in ((a, "origin"), (b, "upstream")):
        await freeze_project(project.specification(), project.workdir / "frozen.yaml", concurrency=1)
        frozen = yaml.safe_load((project.workdir / "frozen.yaml").read_text())
        assert set(frozen["odoo"]) == {alias}
        assert set(frozen["odoo"][alias]) == {"18.0"}
        # Existing freeze output retains Git's trailing newline. This test
        # concerns revision selection, not that unrelated formatting issue.
        assert frozen["odoo"][alias]["18.0"].strip() == latest


@pytest.mark.asyncio
async def test_freeze_of_pinned_project_keeps_old_sha_after_shared_fetch(odoo_store: OdooEnvironment) -> None:
    old = odoo_store.git(odoo_store.remote, "rev-parse", "18.0")
    a, b = odoo_store.project("a"), odoo_store.project("b")
    b.pin(old)
    await a.build()
    await b.build()
    odoo_store.advance()
    await a.build()

    await freeze_project(b.specification(), b.workdir / "frozen.yaml", concurrency=1)

    frozen = yaml.safe_load((b.workdir / "frozen.yaml").read_text())
    assert set(frozen["odoo"]) == {"origin"}
    assert set(frozen["odoo"]["origin"]) == {"18.0"}
    assert frozen["odoo"]["origin"]["18.0"].strip() == old


@pytest.mark.asyncio
async def test_failed_fetch_preserves_source_and_successful_retry_updates_both(odoo_store: OdooEnvironment) -> None:
    a, b = odoo_store.project("a"), odoo_store.project("b")
    await a.build()
    await b.build()
    before = [(p.source.resolve(), odoo_store.git(p.source, "rev-parse", "HEAD")) for p in (a, b)]
    latest = odoo_store.advance()
    unavailable = odoo_store.remote.with_name("temporarily-unavailable")
    odoo_store.remote.rename(unavailable)
    try:
        with pytest.raises(Exception):
            await a.build()
        for project, (source, head) in zip((a, b), before):
            assert project.source.resolve() == source
            assert odoo_store.git(project.source, "rev-parse", "HEAD") == head
            assert (project.source / "odoo/version.txt").read_text() == "old upstream\n"
    finally:
        unavailable.rename(odoo_store.remote)

    await a.build()
    assert odoo_store.git(a.source, "rev-parse", "HEAD") == latest
    assert odoo_store.git(b.source, "rev-parse", "HEAD") == latest


@pytest.mark.asyncio
async def test_published_source_files_are_not_writable(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    await project.build()

    # Check permission bits rather than writing: tests may be run as root.
    assert (project.source / "odoo/message.txt").stat().st_mode & 0o222 == 0


@pytest.mark.asyncio
async def test_existing_clean_checkout_stays_project_local(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    odoo_store.git(project.workdir, "clone", "--branch", "18.0", odoo_store.remote.as_uri(), str(project.source))
    upstream = odoo_store.advance()

    await project.build()

    assert not project.source.is_symlink()
    assert odoo_store.common_dir(project) == project.source / ".git"
    assert odoo_store.git(project.source, "rev-parse", "HEAD") == upstream
    assert not odoo_store.store_root.exists()


@pytest.mark.asyncio
async def test_existing_empty_source_directory_is_not_replaced(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    project.source.mkdir()

    await project.build()

    assert not project.source.is_symlink()
    assert (project.source / ".git").is_dir()
    assert not odoo_store.store_root.exists()


@pytest.mark.asyncio
async def test_dirty_existing_checkout_is_preserved(odoo_store: OdooEnvironment) -> None:
    project = odoo_store.project("a")
    odoo_store.git(project.workdir, "clone", "--branch", "18.0", odoo_store.remote.as_uri(), str(project.source))
    (project.source / "odoo/message.txt").write_text("local work\n")

    with pytest.raises(Exception):
        await project.build()

    assert not project.source.is_symlink()
    assert (project.source / "odoo/message.txt").read_text() == "local work\n"
    assert not odoo_store.store_root.exists()
