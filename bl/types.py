from pathlib import Path
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Dict, List, Optional


class OriginType(Enum):
    """Type of origin reference."""

    BRANCH = "branch"
    PR = "pr"
    REF = "ref"


@dataclass
class Remote:
    name: str
    url: str


class CloneFlags(IntEnum):
    SHALLOW = 1
    SPARSE = 2


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


@dataclass
class CloneInfo:
    url: str
    clone_flags: int
    root_refspec_info: RefspecInfo


class RepoInfo:
    """Represents the specification for a set of modules."""

    def __init__(
        self,
        modules: List[str],
        remotes: List[str] = {},
        refspecs: List[RefspecInfo] = [],
        shell_commands: List[str] = [],
        patch_globs_to_apply: List[str] = [],
        target_folder: Optional[str] = None,
        locales: List[str] = [],
    ):
        self.modules = modules
        self.remotes = remotes
        self.refspec_info = refspecs
        self.shell_commands = shell_commands
        self.patch_globs_to_apply = patch_globs_to_apply
        self.target_folder = target_folder
        self.locales = locales

    def __repr__(self) -> str:
        return f"ModuleSpec(modules={self.modules}, remotes={self.remotes}, origins={self.refspec_info})"


class ProjectSpec:
    """Represents the overall project specification from the YAML file."""

    def __init__(self, repos: Dict[str, RepoInfo], workdir: Path = Path(".")):
        self.repos = repos
        self.workdir = workdir

    def __repr__(self) -> str:
        return f"ProjectSpec(specs={self.repos}, workdir={self.workdir})"
