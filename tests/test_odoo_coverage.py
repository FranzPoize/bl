"""Shared checkout coverage grows independently of source identity."""

import json

import pytest

from bl import odoo_store as store
from tests.odoo_worktree_helpers import OdooEnvironment


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["branch", "pinned", "patch", "merge"])
async def test_expansion_reuses_head_and_checkout(odoo_store: OdooEnvironment, monkeypatch, kind):
    patches = odoo_store.patch_series("patched") if kind == "patch" else []
    a, b = (odoo_store.project(name, patches) for name in ("a", "b"))
    for project, module, locale in ((a, "account", "fr"), (b, "sale", "es")):
        project.configure(modules=[module], locales=[locale])
        if kind == "pinned":
            project.pin(odoo_store.git(odoo_store.remote, "rev-parse", "HEAD"))
        elif kind == "merge":
            project.configure(merges=["origin 18.0"])
    await a.build()
    original = a.source.resolve()
    head = odoo_store.git(a.source, "rev-parse", "HEAD")
    real_git = store._git
    added_worktrees = []

    async def track(*args, **kwargs):
        if args[:2] == ("worktree", "add"):
            added_worktrees.append(args)
        return await real_git(*args, **kwargs)

    monkeypatch.setattr(store, "_git", track)
    await b.build()
    await a.build()

    assert not added_worktrees
    assert b.source.resolve() == original
    assert odoo_store.git(a.source, "rev-parse", "HEAD") == head
    for module, locale in (("account", "fr"), ("sale", "es")):
        path = a.source / "addons" / module / "i18n" / f"{locale}.po"
        assert path.is_file()
        assert not path.stat().st_mode & 0o222
    record = json.loads((odoo_store.store_root / "metadata" / f"{original.name}.json").read_text())
    assert "selection" not in record["recipe"]
    assert record["coverage"] == {"modules": ["account", "sale"], "locales": ["es", "fr"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expanded",
    [
        {"modules": [], "locales": ["fr"]},
        {"modules": ["account"], "locales": []},
        {"modules": [], "locales": []},
    ],
)
async def test_all_selection_never_shrinks_on_later_builds(odoo_store: OdooEnvironment, expanded):
    a, b = odoo_store.project("a"), odoo_store.project("b")
    a.configure(modules=["account"], locales=["fr"])
    b.configure(**expanded)
    await a.build()
    await b.build()
    original = a.source.resolve()
    before = {p.relative_to(original) for p in original.rglob("*") if p.is_file()}
    latest = odoo_store.advance()
    await a.build()
    assert b.source.resolve() == original
    assert odoo_store.git(b.source, "rev-parse", "HEAD") == latest
    assert before <= {p.relative_to(original) for p in original.rglob("*") if p.is_file()}
    if not expanded["modules"]:
        assert (a.source / "addons/sale/i18n/fr.po").is_file()
        if expanded["locales"]:
            assert not (a.source / "addons/sale/i18n/es.po").exists()
    if not expanded["locales"]:
        assert (a.source / "addons/account/i18n/es.po").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("patched", [False, True])
async def test_branch_update_expands_and_preserves_existing_coverage(odoo_store: OdooEnvironment, patched):
    patches = odoo_store.patch_series("patched") if patched else []
    a, b = odoo_store.project("a", patches), odoo_store.project("b", patches)
    a.configure(modules=["account"], locales=["fr"])
    b.configure(modules=["sale"], locales=["es"])
    await a.build()
    original = a.source.resolve()
    latest = odoo_store.advance()
    await b.build()
    await a.build()
    assert a.source.resolve() == b.source.resolve() == original
    if patched:
        assert (a.source / "odoo/message.txt").read_text() == "patched\n"
        assert odoo_store.git(a.source, "rev-parse", "HEAD^") == latest
    else:
        assert odoo_store.git(a.source, "rev-parse", "HEAD") == latest
    assert (a.source / "addons/account/i18n/fr.po").is_file()
    assert (a.source / "addons/sale/i18n/es.po").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("update", [False, True])
async def test_failed_expansion_keeps_previous_coverage(odoo_store: OdooEnvironment, monkeypatch, update):
    a, b = odoo_store.project("a"), odoo_store.project("b")
    a.configure(modules=["account"], locales=["fr"])
    b.configure(modules=["sale"], locales=["es"])
    await a.build()
    original = a.source.resolve()
    record_path = odoo_store.store_root / "metadata" / f"{original.name}.json"
    before = record_path.read_bytes()
    head = odoo_store.git(a.source, "rev-parse", "HEAD")
    if update:
        odoo_store.advance()
    real_git = store._git

    async def fail(*args, **kwargs):
        if args[:2] == ("sparse-checkout", "set") and "/addons/sale/*" in args and kwargs["cwd"] == original:
            raise store.OdooStoreError("expansion failed")
        return await real_git(*args, **kwargs)

    monkeypatch.setattr(store, "_git", fail)
    with pytest.raises(store.OdooStoreError, match="expansion failed"):
        await b.build()
    assert a.source.resolve() == original
    assert record_path.read_bytes() == before
    assert odoo_store.git(a.source, "rev-parse", "HEAD") == head
    assert (a.source / "addons/account/i18n/fr.po").is_file()
    assert not (a.source / "addons/sale").exists()
    assert not b.source.exists()
