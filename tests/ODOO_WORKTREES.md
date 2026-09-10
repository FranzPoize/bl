# Shared Odoo worktree acceptance tests

Branch, patch, and merge sharing are implemented. All acceptance tests are
expected to pass, with no skipped or expected-failure cases.
`test_odoo_basic_sharing.py` covers existing-checkout preservation and changes
to project specs.

The source contract is a moving shared branch: rebuilding either consumer
updates both consumers when their branch, merge sequence, and patches match.
Module and locale selections expand the shared checkout's coverage. Explicitly frozen revisions remain separate and pinned.
Different patch or merge recipes share Git storage, not their working files.

The suites cover:

- `test_odoo_worktree_sharing.py`: shared branch updates, versions, repository
  identity, aliases, target paths, sparse selections, frozen revisions, failure
  recovery, read-only files, and preservation of existing checkouts.
- `test_odoo_worktree_patches.py`: ordered project-relative patches, reuse by
  content, shared updates, changed patches, rebuilds, and conflicts.
- `test_odoo_worktree_merges.py`: ordered merge recipes and shared updates to
  merged branches.
- `test_odoo_worktree_lifecycle.py`: editing restrictions, project cleanup,
  same-named projects, dry runs, and explicit store cleanup.
- `test_odoo_worktree_concurrency.py`: separate build processes synchronized
  with pipes, plus termination after checkout creation and recovery on retry.
- `test_odoo_shared_transforms.py`: cross-remote merges, PR refs, freezing
  individual inputs, legacy patch syntax, sparse patches, and store compatibility.
- `test_main.py`: CLI dispatch, including standalone store cleanup.

`odoo_worktree_helpers.py` creates tiny local Git repositories and isolates
Git identity/configuration and XDG directories. No Odoo download is needed.

Run with the project's test dependencies installed:

```sh
python -m pytest tests
```

Most tests use existing build/freeze/clean/edit entry points. The two store
cleanup tests use `bl.odoo_store.prune_unused_worktrees(root, dry_run=...)`.
The `bl clean-store` CLI exposes this operation with dry-run and confirmation.

Sparse selection tests verify that coverage grows across consumers without
changing worktree identity or losing files on later builds. Empty module or
locale selections mean all. Coverage tests include pinned and transformed
sources, branch updates, rollback, and concurrent expansion.
