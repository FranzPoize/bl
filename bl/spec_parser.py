import yaml
import re
from typing import List, Dict, Any, Optional
from enum import Enum


class OriginType(Enum):
    """Type of origin reference."""

    BRANCH = "branch"
    PR = "pr"
    REF = "ref"


class ModuleOrigin:
    """Represents an origin reference for a module."""

    def __init__(
        self,
        remote: str,
        origin: str,
        type: OriginType,
    ):
        self.remote = remote
        self.origin = origin
        self.type = type

    def __repr__(self) -> str:
        return f"ModuleOrigin(remote={self.remote!r}, origin={self.origin!r}, type={self.type.value})"


class ModuleSpec:
    """Represents the specification for a set of modules."""

    def __init__(
        self,
        modules: List[str],
        remotes: Optional[Dict[str, str]] = None,
        origins: Optional[List[ModuleOrigin]] = None,
        shell_commands: Optional[List[str]] = None,
        patch_globs_to_apply: Optional[List[str]] = None,
    ):
        self.modules = modules
        self.remotes = remotes
        self.origins = origins
        self.shell_commands = shell_commands
        self.patch_globs_to_apply = patch_globs_to_apply

    def __repr__(self) -> str:
        return f"ModuleSpec(modules={self.modules}, remotes={self.remotes}, origins={self.origins})"


class ProjectSpec:
    """Represents the overall project specification from the YAML file."""

    def __init__(self, specs: Dict[str, ModuleSpec]):
        self.specs = specs

    def __repr__(self) -> str:
        return f"ProjectSpec(specs={self.specs})"


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

        specs: Dict[str, ModuleSpec] = {}
        for section_name, section_data in data.items():
            modules = section_data.get("modules", [])
            src = section_data.get("src")
            remotes = section_data.get("remotes") or {}
            merges = section_data.get("merges") or []
            shell_commands = section_data.get("shell_command_after") or None
            patch_globs_to_apply = section_data.get("patch_globs") or None

            # Parse merges into ModuleOrigin objects
            origins: List[ModuleOrigin] = []
            for merge_entry in merges:
                parts = merge_entry.split(" ", 1)
                if len(parts) == 2:
                    remote_key = parts[0]
                    origin_value = parts[1]

                    # Determine type: PR if matches refs/pull/{pr_id}/head pattern, otherwise branch
                    origin_type = get_origin_type(origin_value)

                    origins.append(ModuleOrigin(remote_key, origin_value, origin_type))

            # Decompose src into remote and origin, then add to remotes and origins
            if src:
                parts = src.split(" ", 1)
                if len(parts) == 2:
                    remote_url = parts[0]
                    origin_value = parts[1]

                    # Extract repository name as key from URL
                    # Handle both https://github.com/org/repo and git@github.com:org/repo.git formats
                    if remote_url.startswith("git@"):
                        # git@github.com:org/repo.git -> extract repo name
                        repo_part = remote_url.split(":")[-1].rstrip(".git")
                        remote_key = repo_part.split("/")[-1]
                    else:
                        # https://github.com/org/repo -> extract repo name
                        remote_key = remote_url.rstrip("/").split("/")[-1]

                    # Add to remotes dict
                    remotes[remote_key] = remote_url

                    # Determine type: PR if matches refs/pull/{pr_id}/head pattern, otherwise branch
                    origin_type = get_origin_type(origin_value)

                    # Add to origins list
                    origins.append(ModuleOrigin(remote_key, origin_value, origin_type))

            specs[section_name] = ModuleSpec(
                modules,
                remotes if remotes else None,
                origins if origins else None,
                shell_commands,
                patch_globs_to_apply,
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


if __name__ == "__main__":
    # Example usage:
    spec_file = "spec.yaml"
    project_spec = load_spec_file(spec_file)

    if project_spec:
        print("Successfully loaded project specification:")
        # You can access the data like this:
        # print(project_spec.specs['odoo'].modules)
        # print(project_spec.specs['server-ux'].remotes)
        # print(project_spec.specs['server-ux'].origins)
        print([spec for name, spec in project_spec.specs.items() if name == "queue"])
