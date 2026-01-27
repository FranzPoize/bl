import asyncio
import os
from pathlib import Path
from typing import Optional

import warnings
from bl.spec_parser import ModuleSpec, OriginType, RefspecInfo


english_env = os.environ.copy()
# Ensure git outputs in English for consistent parsing
english_env["LANG"] = "en_US.UTF-8"


def get_module_path(workdir: Path, module_name: str, module_spec: ModuleSpec) -> Path:
    """Returns the path to the module directory."""
    if module_name == "odoo" and module_spec.target_folder is None:
        warnings.warn(
            "importing 'odoo' without a 'target_folder' "
            + "property is deprecated. Use target_folder: 'src/' in spec.yaml.",
            DeprecationWarning,
        )
        return workdir / "src/"
    elif module_spec.target_folder is not None:
        return workdir / module_spec.target_folder
    else:
        return workdir / "external-src" / module_name


def get_local_ref(origin: RefspecInfo) -> str:
    """Generates a local reference name for a given origin."""
    return f"loc-{origin.ref_name or origin.refspec}"


async def run_git(*args: str, cwd: Optional[Path] = None) -> tuple[int, str, str]:
    """Executes a git command asynchronously."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        env=english_env,
    )
    stdout, stderr = await proc.communicate()
    returncode = proc.returncode if proc.returncode is not None else -1
    return returncode, stdout.decode().strip(), stderr.decode().strip()
