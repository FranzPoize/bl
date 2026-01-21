### Task: Tests and validation

- Add parser-level tests that load a sample spec plus `frozen.yaml` (see structure in `dev_folder/frozen_sha_research.md`) and assert `frozen_sha` and `frozen_modules` are populated as expected.
- Add processor integration tests (or high-value smoke) that clone from lightweight fixtures and verify frozen SHAs are honored for REF/BRANCH/PR cases.
- Document expected behaviour when freezes are missing or partial, and how to add/update `frozen.yaml` entries for new refspecs.
- Optionally surface a CLI/debug flag to print applied freezes, aiding validation during runs.
