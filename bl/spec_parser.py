import re
import warnings
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class OriginType(Enum):
    """Type of origin reference."""

    BRANCH = "branch"
    PR = "pr"
    REF = "ref"


def make_remote_merge_from_src(src: str) -> tuple[dict, list]:
    """
    Creates a remote and merge entry from the src string.
    """
    remotes = {}
    merges = []

    parts = src.split(" ", 1)
    remotes["origin"] = parts[0]
    merges.append(f"origin {parts[1]}")

    return remotes, merges


def get_origin_type(origin_value: str) -> OriginType:
    """
    Determines the origin type based on the origin value.

    Args:
        origin_value: The origin string to evaluate.

    Returns:
        The corresponding OriginType.
    """
    # Pattern to match GitHub PR references: refs/pull/{pr_id}/head
    pr_pattern = re.compile(r"^refs/pull/\d+/head$")
    # Pattern to match that matches git reference hashes (40 hex characters)
    ref_pattern = re.compile(r"^[a-z0-9]{40}$")

    if pr_pattern.match(origin_value):
        return OriginType.PR
    elif ref_pattern.match(origin_value):
        return OriginType.REF
    else:
        return OriginType.BRANCH


class RefspecInfo:
    """A git refspec with its remote, type and optional frozen sha."""

    def __init__(
        self,
        remote: str,
        ref_str: str,
        type: OriginType,
        ref_name: Optional[str],
    ):
        self.remote = remote
        self.refspec = ref_str
        """ The refspec string (branch name, PR ref, or commit hash). """
        self.type = type
        self.ref_name = ref_name

    def __repr__(self) -> str:
        return f"RefspecInfo(remote={self.remote!r}, origin={self.refspec!r}, type={self.type.value})"


class ModuleSpec:
    """Represents the specification for a set of modules."""

    def __init__(
        self,
        modules: List[str],
        remotes: Optional[Dict[str, str]] = {},
        origins: Optional[List[RefspecInfo]] = [],
        shell_commands: Optional[List[str]] = [],
        patch_globs_to_apply: Optional[List[str]] = None,
        target_folder: Optional[str] = None,
        frozen_modules: Optional[Dict[str, Dict[str, str]]] = None,
        locales: Optional[List[str]] = [],
    ):
        self.modules = modules
        self.remotes = remotes
        self.refspec_info = origins
        self.shell_commands = shell_commands
        self.patch_globs_to_apply = patch_globs_to_apply
        self.frozen_modules = frozen_modules
        self.target_folder = target_folder
        self.locales = locales

    def __repr__(self) -> str:
        return f"ModuleSpec(modules={self.modules}, remotes={self.remotes}, origins={self.refspec_info})"


class ProjectSpec:
    """Represents the overall project specification from the YAML file."""

    def __init__(self, specs: Dict[str, ModuleSpec], workdir: Path = Path(".")):
        self.specs = specs
        self.workdir = workdir

    def __repr__(self) -> str:
        return f"ProjectSpec(specs={self.specs}, workdir={self.workdir})"


def load_spec_file(config: Path, frozen: Path, workdir: Path) -> Optional[ProjectSpec]:
    """
    Loads and parses the project specification from a YAML file.

    Args:
        file_path: The path to the YAML specification file.

    Returns:
        A ProjectSpec object if successful, None otherwise.
    """
    if not config.exists():
        if config.is_relative_to("."):
            config = config.resolve()
            # If the file is not in the current directory, check inside the odoo subdirectory
            odoo_config = config.parent / "odoo" / config.name
            if not odoo_config.exists():
                print(f"Error: Neither '{config}' nor '{odoo_config}' exists.")
                return None
            config = odoo_config
        else:
            print(f"Error: File '{config}' does not exist.")
            return None

    workdir = workdir or config.parent

    with config.open("r") as f:
        try:
            data: Dict[str, Any] = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"Error parsing YAML file '{config}': {e}")
            return None

    frozen_mapping: Dict[str, Dict[str, Dict[str, str]]] = {}
    frozen_path = frozen or Path(config).with_name("frozen.yaml")
    if frozen_path.exists():
        try:
            with frozen_path.open("r") as frozen_file:
                loaded_freezes = yaml.safe_load(frozen_file) or {}
                if isinstance(loaded_freezes, dict):
                    frozen_mapping = loaded_freezes
        except yaml.YAMLError as e:
            print(f"Error parsing frozen YAML file '{frozen_path}': {e}")

    specs: Dict[str, ModuleSpec] = {}
    for section_name, section_data in data.items():
        modules = section_data.get("modules", [])
        src = section_data.get("src")
        remotes = section_data.get("remotes") or {}
        merges = section_data.get("merges") or []
        shell_commands = section_data.get("shell_command_after") or None
        patch_globs_to_apply = section_data.get("patch_globs") or None
        target_folder = section_data.get("target_folder") or None
        locales = section_data.get("locales", [])

        frozen_for_section_raw = frozen_mapping.get(section_name)
        frozen_for_section: Optional[Dict[str, Dict[str, str]]] = (
            frozen_for_section_raw if isinstance(frozen_for_section_raw, dict) else None
        )

        # Parse merges into RefspecInfo objects
        origins: List[RefspecInfo] = []
        if src:
            # If src is defined, create a remote and merge entry from it
            src_remotes, src_merges = make_remote_merge_from_src(src)
            remotes.update(src_remotes)
            merges = src_merges + merges

        for merge_entry in merges:
            parts = merge_entry.split(" ", 2)
            if len(parts) == 2:
                remote_key, ref_spec = parts

                # Determine type: PR if matches refs/pull/{pr_id}/head pattern, otherwise branch
                ref_type = get_origin_type(ref_spec)

                ref_name = None
                if frozen_for_section:
                    remote_freezes = frozen_for_section.get(remote_key) or {}
                    ref_name = ref_spec
                    ref_type = OriginType.REF
                    frozen_ref = remote_freezes.get(ref_spec)
                    ref_spec = frozen_ref or ref_spec

                origins.append(
                    RefspecInfo(
                        remote_key,
                        ref_spec,
                        ref_type,
                        ref_name,
                    )
                )
            elif len(parts) == 3:
                warnings.warn(
                    "Deprecated src format: use <url> <sha> format for the src property",
                    DeprecationWarning,
                )
                remote_key, _, ref_spec = parts
                ref_type = get_origin_type(ref_spec)

                ref_name = None
                if frozen_for_section:
                    remote_freezes = frozen_for_section.get(remote_key) or {}
                    ref_name = ref_spec
                    ref_spec = remote_freezes.get(ref_spec)

                origins.append(RefspecInfo(remote_key, ref_spec, ref_type, ref_name))

        specs[section_name] = ModuleSpec(
            modules,
            remotes,
            origins,
            shell_commands,
            patch_globs_to_apply,
            target_folder,
            frozen_for_section,
            locales,
        )

    return ProjectSpec(specs, workdir)
