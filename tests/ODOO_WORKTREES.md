# Shared Odoo worktree acceptance tests

Basic unpatched branch sharing is implemented. Acceptance tests for shared
patches and merges remain deliberately failing; they are not marked `xfail`
or skipped. `test_odoo_basic_sharing.py` covers the boundary between the basic
implementation and the existing project-local patch/merge processing.

The source contract is a moving shared branch: rebuilding either consumer
updates both consumers when their branch, merge sequence, patches, and checkout
selections match. Explicitly frozen revisions remain separate and pinned.
Different patch or merge recipes share Git storage, not their working files.

The suites cover:

- `test_odoo_worktree_sharing.py`: shared branch updates, versions, repository
  identity, aliases, target paths, sparse selections, frozen revisions, failure
  recovery, read-only files, and migration of existing checkouts.
- `test_odoo_worktree_patches.py`: ordered project-relative patches, reuse by
  content, shared updates, changed patches, rebuilds, and conflicts.
- `test_odoo_worktree_merges.py`: ordered merge recipes and shared updates to
  merged branches.
- `test_odoo_worktree_lifecycle.py`: editing restrictions, project cleanup,
  same-named projects, dry runs, and explicit store cleanup.
- `test_odoo_worktree_concurrency.py`: separate build processes synchronized
  with pipes, plus termination after checkout creation and recovery on retry.

`odoo_worktree_helpers.py` creates tiny local Git repositories and isolates
Git identity/configuration and XDG directories. No Odoo download is needed.

Run with the project's test dependencies installed:

```sh
python -m pytest tests/test_odoo_worktree_*.py
```

Most tests use existing build/freeze/clean/edit entry points. The two store
cleanup tests use `bl.odoo_store.prune_unused_worktrees(root, dry_run=...)`.
This is an internal API; there is no store cleanup CLI command yet.

Sparse selection tests preserve existing module/language behavior. They do
not assume the proposed complete-checkout mode has been accepted. The tests
also avoid prescribing recipe hash encodings or metadata serialization details.
