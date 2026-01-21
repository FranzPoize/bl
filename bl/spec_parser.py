import warnings
import yaml
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from enum import Enum


class OriginType(Enum):
    """Type of origin reference."""

    BRANCH = "branch"
    PR = "pr"
    REF = "ref"


def make_remote_merge_from_src(src: str) -> str:
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
        frozen_sha: Optional[str] = None,
    ):
        self.remote = remote
        self.refspec = ref_str
        """ The refspec string (branch name, PR ref, or commit hash). """
        self.type = type
        self.frozen_sha = frozen_sha

    def __repr__(self) -> str:
        return (
            "RefspecInfo("
            f"remote={self.remote!r}, origin={self.refspec!r}, type={self.type.value}, "
            f"frozen_sha={self.frozen_sha!r})"
        )


class ModuleSpec:
    """Represents the specification for a set of modules."""

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
        self.modules = modules
        self.remotes = remotes
        self.refspec_info = origins
        self.shell_commands = shell_commands
        self.patch_globs_to_apply = patch_globs_to_apply
        self.frozen_modules = frozen_modules
        self.target_folder = None

    def __repr__(self) -> str:
        return f"ModuleSpec(modules={self.modules}, remotes={self.remotes}, origins={self.refspec_info})"


class ProjectSpec:
    """Represents the overall project specification from the YAML file."""

    def __init__(self, specs: Dict[str, ModuleSpec]):
        self.specs = specs

    def __repr__(self) -> str:
        return f"ProjectSpec(specs={self.specs})"


def load_spec_file(file_path: str) -> Optional[ProjectSpec]:
    """
    Loads and parses the project specification from a YAML file.

    Args:
        file_path: The path to the YAML specification file.

    Returns:
        A ProjectSpec object if successful, None otherwise.
    """
    try:
        with open(file_path, "r") as f:
            data: Dict[str, Any] = yaml.safe_load(f)

        frozen_mapping: Dict[str, Dict[str, Dict[str, str]]] = {}
        frozen_path = Path(file_path).with_name("frozen.yaml")
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
                    remote_key = parts[0]
                    origin_value = parts[1]

                    # Determine type: PR if matches refs/pull/{pr_id}/head pattern, otherwise branch
                    origin_type = get_origin_type(origin_value)

                    frozen_sha = None
                    if frozen_for_section:
                        remote_freezes = frozen_for_section.get(remote_key) or {}
                        frozen_sha = remote_freezes.get(origin_value)

                    origins.append(
                        RefspecInfo(
                            remote_key,
                            origin_value,
                            origin_type,
                            frozen_sha=frozen_sha,
                        )
                    )
                elif len(parts) == 3:
                    warnings.warn(
                        "Deprecated src format: use <url> <sha> format for the src property",
                        DeprecationWarning,
                    )
                    remote_key = parts[0]
                    origin_value = parts[2]

                    origin_type = get_origin_type(origin_value)

                    frozen_sha = None
                    if frozen_for_section:
                        remote_freezes = frozen_for_section.get(remote_key) or {}
                        frozen_sha = remote_freezes.get(origin_value)

                    origins.append(
                        RefspecInfo(
                            remote_key,
                            origin_value,
                            origin_type,
                            frozen_sha=frozen_sha,
                        )
                    )

            specs[section_name] = ModuleSpec(
                modules,
                remotes if remotes else None,
                origins if origins else None,
                shell_commands,
                patch_globs_to_apply,
                frozen_modules=frozen_for_section or None,
            )

        return ProjectSpec(specs)

    except FileNotFoundError:
        print(f"Error: The file '{file_path}' was not found.")
        return None
    except yaml.YAMLError as e:
        print(f"Error parsing YAML file '{file_path}': {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while processing '{file_path}': {e}")
        return None
