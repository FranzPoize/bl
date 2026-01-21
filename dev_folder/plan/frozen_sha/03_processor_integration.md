### Task: Use frozen SHAs during processing

- Reconcile the `ModuleOrigin` vs. `RefspecInfo` import noted in `dev_folder/frozen_sha_research.md`; ensure processor uses the enriched origin objects.
- When `frozen_sha` is present, prefer it for checkouts/resets (REF) and post-fetch hard resets (BRANCH/PR), keeping existing fetch/merge logic.
- Ensure merge sequencing still works when the base ref is checked out at a frozen SHA and subsequent merges need their own freezes (if provided).
- Consider logging or dry-run output that surfaces which frozen SHAs were applied for traceability.
