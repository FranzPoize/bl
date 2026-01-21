### Goal

Populate the `frozen_sha` property on each `RefspecInfo` when parsing a spec YAML file, using a `frozen.yaml` file that lives next to the spec file (same directory). The `frozen.yaml` file maps each module group (e.g. `sale-promotion`) to remotes (`ak`, `oca`, …), and then to refspecs (branches / PR refs / tags) and their corresponding frozen commit hashes.

### Relevant data shape (from `dev_folder/reference/frozen.yaml`)

- **Top-level keys**: logical module groups, e.g. `sale-promotion`, `queue`, `shopinvader`, etc.  
  - These correspond to the keys used in `ProjectSpec.specs` (section names in `noukies-spec.yaml`).
- **Second-level keys**: git remotes for that logical group, e.g. `ak`, `oca`, `origin`, `shopinvader`, etc.
- **Leaf keys**: refspec strings that currently appear in `merges` (or `src`) in the spec, e.g.:
  - Branch names: `'14.0'`, `14.0-sale_coupon_invoice_delivered`, etc.
  - PR refs: `refs/pull/188/head`, etc.
- **Leaf values**: 40‑char commit hashes (frozen SHAs).

So the effective structure we care about is:

```python
FrozenMapping = dict[str, dict[str, dict[str, str]]]
# { module_group: { remote: { refspec: sha } } }
```

### Current parser / model (`bl/spec_parser.py`)

- **Types / helpers**
  - `OriginType` enum:
    - `BRANCH`, `PR`, `REF`
  - `get_origin_type(origin_value: str) -> OriginType`
    - Detects:
      - `PR` if it matches `^refs/pull/\d+/head$`
      - `REF` if it is a 40‑character lowercase hex string
      - Otherwise `BRANCH`
- **`RefspecInfo`**
  - Currently defined as:

    ```python
    class RefspecInfo:
        def __init__(self, remote: str, ref_str: str, type: OriginType):
            self.remote = remote
            self.refspec = ref_str
            self.type = type
    ```

  - There is **no `frozen_sha` attribute yet**.
- **`ModuleSpec`**
  - Constructor signature includes `frozen_modules` but type is untyped/opaque:

    ```python
    class ModuleSpec:
        def __init__(
            self,
            modules: List[str],
            remotes: Optional[Dict[str, str]] = None,
            origins: Optional[List[RefspecInfo]] = None,
            shell_commands: Optional[List[str]] = None,
            patch_globs_to_apply: Optional[List[str]] = None,
            target_folder: Optional[str] = None,
            frozen_modules=None,
        ):
            self.frozen_modules = frozen_modules
    ```

  - `target_folder` is hard-coded to `None` instead of the argument, which is unrelated but worth noting.
- **`ProjectSpec`**
  - Simple wrapper around `specs: Dict[str, ModuleSpec]`.
- **`load_spec_file(file_path: str)`**
  - Reads the YAML spec (`noukies-spec.yaml` or similar) with `yaml.safe_load`.
  - For each top-level `section_name`/`section_data`:
    - Reads:
      - `modules`
      - `src`
      - `remotes`
      - `merges`
      - `shell_command_after`
      - `patch_globs`
    - If `src` is set:
      - Constructs `remotes`/`merges` from `make_remote_merge_from_src(src)`.
    - For each entry in `merges`:
      - Splits into `remote_key` and `origin_value`.
      - Builds `RefspecInfo(remote_key, origin_value, get_origin_type(origin_value))`.
    - Creates:

      ```python
      specs[section_name] = ModuleSpec(
          modules,
          remotes if remotes else None,
          origins if origins else None,
          shell_commands,
          patch_globs_to_apply,
      )
      ```

  - **No knowledge of `frozen.yaml` yet**.

### Current processor (`bl/spec_processor.py`)

> Important to know where `RefspecInfo` is subsequently used.

- Imports (mismatch to note):

  ```python
  from .spec_parser import ProjectSpec, ModuleSpec, ModuleOrigin, OriginType
  ```

  - `ModuleOrigin` does **not** exist in `spec_parser.py`. It looks like this should actually be `RefspecInfo`, or there was a refactor in progress (good to be aware of when wiring `frozen_sha` in the future).
- `SpecProcessor.process_module(...)`:
  - Uses `spec.refspec_info` (list of origins) to:
    - Choose the first origin as the base (`root_refspec_info`).
    - Determine remote URL from `spec.remotes` or directly from the remote name.
    - Clone (`create_clone_args`) or reset to `root_refspec_info.remote/root_refspec_info.refspec`.
    - Fetch and merge subsequent origins.
  - `create_clone_args(base_origin: ModuleOrigin, remote_url: str)`:
    - If `base_origin.type == OriginType.REF`:
      - Uses `--revision base_origin.refspec` (commit SHA).
    - Else:
      - Uses `--origin base_origin.remote --single-branch --branch base_origin.refspec`.
  - `try_merge` and `_get_local_ref` also use the `type`, `remote`, and `refspec` attributes.
- **Today, no code reads `ModuleSpec.frozen_modules` or any `frozen_sha`**.

### Behaviour we need to add (conceptual)

1. **Locate and read `frozen.yaml`** for a given spec file:
   - When `load_spec_file(file_path)` is called:
     - Compute sibling path, e.g. `frozen_path = Path(file_path).with_name("frozen.yaml")`.
     - If it exists:
       - Load with `yaml.safe_load`.
       - Expect the `FrozenMapping` structure described above.
2. **For each `section_name` (module group)** being parsed from the spec:
   - Look up freezes for that key in the frozen data:

     ```python
     frozen_for_section = frozen_mapping.get(section_name, {})
     # type: dict[remote, dict[refspec, sha]]
     ```

3. **For each `RefspecInfo` we create**:
   - After determining `remote_key` and `origin_value`:
     - Try to look up a frozen SHA:

       ```python
       sha = None
       if frozen_for_section:
           remote_freezes = frozen_for_section.get(remote_key, {})
           sha = remote_freezes.get(origin_value)
       ```

   - If `sha` exists:
     - Set `refspec_info.frozen_sha = sha`.
     - Optionally, also store it in `ModuleSpec.frozen_modules` for easier access by processors.

4. **Populate `ModuleSpec.frozen_modules`**:
   - Make it carry the section‑level mapping for convenience:

     ```python
     frozen_modules: Optional[dict[str, dict[str, str]]]
     # { remote: { refspec: sha } } for that section
     ```

   - When building `ModuleSpec` in `load_spec_file`, pass:

     ```python
     frozen_for_section or None
     ```

5. **Expose `frozen_sha` cleanly in `RefspecInfo`**:
   - Extend `RefspecInfo.__init__` to accept an optional `frozen_sha: Optional[str] = None`.
   - Store as `self.frozen_sha`.
   - Update `__repr__` accordingly to include it when present.

### Code changes we would look at / touch

- **In `bl/spec_parser.py`**
  - **`RefspecInfo`**
    - Add an optional `frozen_sha` field:

      ```python
      class RefspecInfo:
          def __init__(
              self,
              remote: str,
              ref_str: str,
              type: OriginType,
              frozen_sha: Optional[str] = None,
          ):
              self.remote = remote
              self.refspec = ref_str
              self.type = type
              self.frozen_sha = frozen_sha
      ```

  - **`ModuleSpec`**
    - Give `frozen_modules` a concrete typed structure:

      ```python
      class ModuleSpec:
          def __init__(
              self,
              modules: List[str],
              remotes: Optional[Dict[str, str]] = None,
              origins: Optional[List[RefspecInfo]] = None,
              shell_commands: Optional[List[str]] = None,
              patch_globs_to_apply: Optional[List[str]] = None,
              target_folder: Optional[str] = None,
              frozen_modules: Optional[Dict[str, Dict[str, str]]] = None,
          ):
              self.frozen_modules = frozen_modules
      ```

  - **`load_spec_file(file_path: str)`**
    - At the top, after reading the main YAML:
      - Resolve a sibling `frozen.yaml` path and, if present, load it into a `FrozenMapping`.
    - Inside the loop over `section_name, section_data`:
      - Compute `frozen_for_section` from the mapping.
      - While building each `RefspecInfo`, look up the SHA in `frozen_for_section` and pass it to the constructor.
      - Pass `frozen_for_section` into the `ModuleSpec` constructor.

- **In `bl/spec_processor.py` (future integration)**
  - Even though the user request here is only about populating `frozen_sha` in the parser, we should be aware of the usage points:
    - `create_clone_args` / `process_module`:
      - For `OriginType.REF` (commit hashes), we may eventually want to prefer `frozen_sha` over `refspec` when present.
      - For branches/PRs, we *could*:
        - Fetch the branch/PR, then hard‑reset to the frozen SHA.
        - Or use `frozen_sha` as the checkout revision directly and still track remotes/refspecs for fetch/merge logic.
    - Any change here must respect the existing flow (shallow clones, `--branch`, sparse checkout).
  - The import of `ModuleOrigin` will need reconciliation with `RefspecInfo` if we start passing richer origin objects around.

### Open design questions / assumptions

- **Multiple spec files**: we assume each spec YAML file uses **only the `frozen.yaml` next to it**, not a global one.
- **Case sensitivity & quoting**:
  - `frozen.yaml` uses both quoted (`'14.0'`) and unquoted keys; `yaml.safe_load` already normalizes these to Python strings.
  - Refspecs like `refs/pull/188/head` must match exactly the strings used in `merges`.
- **Incomplete freezes**:
  - Some `section_name` entries in the spec may have **no** entry in `frozen.yaml`, or only for some remotes/refspecs.
  - For those, we should:
    - Leave `frozen_sha = None`.
    - Possibly still carry an empty mapping in `ModuleSpec.frozen_modules` or just `None`.

### Validation & debugging

- **Parser validation** (`tests/test_frozen_parser.py`):
  - `test_frozen_sha_populated_from_mapping`: Verifies that `RefspecInfo.frozen_sha` matches the mapping for each remote/refspec.
  - `test_frozen_modules_none_when_section_missing`: Verifies that `ModuleSpec.frozen_modules` is `None` for sections that don't appear in `frozen.yaml`.
  - `test_frozen_sha_none_when_refspec_missing`: Verifies that `frozen_sha` is `None` when a refspec has no entry in the frozen mapping.
- **Processor validation** (`tests/test_frozen_processor.py`):
  - `test_processor_honors_frozen_sha_for_branch`: Creates a local git repo with a branch that has two commits, freezes the first commit for that branch, runs the processor, and checks that the checked‑out HEAD is the frozen SHA, not the branch tip.
- **Running tests**:
  - Run all tests: `pytest tests/`
  - Run parser tests: `pytest tests/test_frozen_parser.py`
  - Run processor tests: `pytest tests/test_frozen_processor.py`
- **Debugging applied freezes**:
  - Setting the environment variable `BL_DEBUG_FREEZES=1` causes the processor to print which frozen SHAs are used for the base and each merge origin while still updating the Rich progress UI.


This is the set of code and data we need to look at to implement `frozen_sha` population in the parser, wired to a sibling `frozen.yaml` file like the example in `dev_folder/reference/frozen.yaml`.

