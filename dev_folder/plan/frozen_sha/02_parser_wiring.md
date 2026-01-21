### Task: Load freezes and enrich parser models

- Follow the flow described in `dev_folder/frozen_sha_research.md` to load a sibling `frozen.yaml` in `load_spec_file`.
- Extend `RefspecInfo` to accept/store an optional `frozen_sha`; propagate it when building origins using the frozen mapping for each section.
- Give `ModuleSpec.frozen_modules` the structured mapping (`dict[remote][refspec]=sha`) and pass section-level freezes into the constructor.
- Keep behaviour identical when no freezes exist (no file or missing keys) — `frozen_sha` stays `None`, mapping stays `None` or empty.
