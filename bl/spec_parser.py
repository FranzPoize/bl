import re
import warnings
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from bl.types import RepoInfo, OriginType, ProjectSpec, RefspecInfo


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


def parse_remote_refspec_from_parts(parts: List[str], frozen_repo: Dict[str, Dict[str, str]]):
    if len(parts) == 2:
        parts.insert(1, "")
    else:
        warnings.warn(
            "Deprecated src format: use <url> <sha> format for the src property",
            DeprecationWarning,
        )
    remote_key, _, ref_spec = parts
    ref_type = get_origin_type(ref_spec)

    ref_name = None
    remote_freezes = frozen_repo.get(remote_key, {})

    if ref_spec in remote_freezes:
        ref_type = OriginType.REF
        ref_name = ref_spec
        ref_spec = remote_freezes.get(ref_name)

    return RefspecInfo(remote_key, ref_spec, ref_type, ref_name)


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
            # TODO(franz): should use rich console for prettiness
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

    repos: Dict[str, RepoInfo] = {}
    for repo_name, repo_data in data.items():
        modules = repo_data.get("modules", [])
        src = repo_data.get("src")
        remotes = repo_data.get("remotes") or {}
        merges = repo_data.get("merges") or []
        shell_commands = repo_data.get("shell_command_after") or None
        patch_globs_to_apply = repo_data.get("patch_globs") or None
        target_folder = repo_data.get("target_folder") or None
        locales = repo_data.get("locales", [])

        frozen_repo = frozen_mapping.get(repo_name, {})

        # Parse merges into RefspecInfo objects
        refspec_infos: List[RefspecInfo] = []
        if src:
            # If src is defined, create a remote and merge entry from it
            src_remotes, src_merges = make_remote_merge_from_src(src)
            remotes.update(src_remotes)
            merges = src_merges + merges

        for merge_entry in merges:
            parts = merge_entry.split(" ", 2)
            refspec_info = parse_remote_refspec_from_parts(parts, frozen_repo)
            refspec_infos.append(refspec_info)

        repos[repo_name] = RepoInfo(
            modules,
            remotes,
            refspec_infos,
            shell_commands,
            patch_globs_to_apply,
            target_folder,
            locales,
        )

    return ProjectSpec(repos, workdir)
