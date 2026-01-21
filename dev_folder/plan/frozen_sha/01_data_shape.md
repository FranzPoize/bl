### Task: Confirm frozen mapping inputs

- **Contract reminder**: `FrozenMapping = dict[str, dict[str, dict[str, str]]]`  
  - `{ module_group: { remote: { refspec: sha } } }`, as described in `dev_folder/frozen_sha_research.md`.

- **Reference `frozen.yaml` shape (`dev_folder/reference/frozen.yaml`)**
  - Top‑level keys are logical module groups (e.g. `sale-promotion`, `queue`, `shopinvader`, `account-analytic`, …).
  - Second‑level keys are remotes (`origin`, `ak`, `oca`, `shopinvader`, `odoo`, …).
  - Leaf keys are refspec strings that also appear in specs (branches like `'14.0'`, named branches like `14.0-sale_coupon_invoice_delivered`, PR refs like `refs/pull/188/head`, etc.).
  - Leaf values are 40‑character commit SHAs.

- **Alignment with real spec (`noukies-spec.yaml`)**
  - Parsed `noukies-spec.yaml` and compared its top‑level section names with the keys in `frozen.yaml`.
  - Counts: **114** sections in `noukies-spec.yaml`, **100** sections in `frozen.yaml`, with **96** in common.
  - Examples **only in spec** (no freezes today): `noukies`, `noukies-public`, `noukies-fix-import`, `ak-odoo-incubator`, `sale-channel`, `sale-import-amazon`, `queue-job_chunk`, `support`, etc.
  - Examples **only in frozen** (harmless extras): `product-attribute_product_pricelist_revision`, `rma`, `sale-import`, `shopinvader_image`.
  - Conclusion: the sections that matter for freezes (e.g. `sale-promotion`, `queue`, `shopinvader`, `account-*`, `pos*`, etc.) have matching keys in both places; additional sections can safely operate without freezes.

- **Decision on missing entries**
  - If a **section name** is missing from `frozen.yaml`: treat it as “no freezes for this section”.
  - If a **remote** or **refspec** is missing under an existing section: treat it as “no freeze for this origin”.
  - Concretely in the parser:
    - `RefspecInfo.frozen_sha` will be set to **`None`** when no matching SHA is found.
    - `ModuleSpec.frozen_modules` will be either the `{remote: {refspec: sha}}` mapping for that section or **`None`** if there is no entry at all for the section (don’t manufacture empty dicts).
  - This matches the assumptions in `dev_folder/frozen_sha_research.md`: freezes are **best‑effort hints**, not mandatory; absence of data must not be treated as an error.

