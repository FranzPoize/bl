import asyncio
import logging
import os
import warnings
from pathlib import Path
from typing import Optional

from bl.types import RefspecInfo, RepoInfo

english_env = os.environ.copy()
# Ensure git outputs in English for consistent parsing
english_env["LANG"] = "en_US.UTF-8"

logger = logging.getLogger(__name__)


def format_diff(diff_message):
    split_message = diff_message.split("\n")

    if len(split_message) > 5:
        split_message = split_message[:5] + ["..."]

    return "\n".join(split_message)


def get_module_path(workdir: Path, module_name: str, module_spec: RepoInfo) -> Path:
    """Returns the path to the module directory."""
    if module_name == "odoo" and not module_spec.target_folder:
        warnings.warn(
            "importing 'odoo' without a 'target_folder' "
            + "property is deprecated. Use target_folder: 'src/' in spec.yaml.",
            DeprecationWarning,
        )
        return workdir / "src/"
    elif module_spec.target_folder:
        return workdir / module_spec.target_folder
    else:
        return workdir / "external-src" / module_name


def get_local_ref(origin: RefspecInfo) -> str:
    """Generates a local reference name for a given origin."""
    return f"{origin.ref_name or origin.refspec}"


async def run(*args: str, cwd: Optional[Path] = None) -> tuple[int, str, str]:
    """Executes a command asynchronously."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        env=english_env,
    )
    stdout, stderr = await proc.communicate()
    returncode = proc.returncode if proc.returncode is not None else -1
    logger.debug(f"In {str(cwd)}: {' '.join([str(a) for a in args])}")
    logger.debug(f"{returncode} - {stdout.decode().strip()} - {stderr.decode().strip()}")
    return returncode, stdout.decode(), stderr.decode()


async def run_git(*args: str, cwd: Optional[Path] = None) -> tuple[int, str, str]:
    """Executes a git command asynchronously."""
    return await run(*["git", "--git-dir", ".git/", *args], cwd=cwd)


async def unlink_path(path: Path) -> tuple[int, str]:
    """Removes a symlink, unmounts a mount, or deletes an empty directory."""
    try:
        if path.is_symlink():
            await asyncio.to_thread(os.unlink, path)
        elif path.is_mount():
            ret, out, err = await run("umount", str(path))
            if ret != 0:
                return -1, f"Failed to unmount {path}: {out} {err}"
        if path.exists() and path.is_dir() and not path.is_mount():
            await asyncio.to_thread(path.rmdir)
    except OSError as e:
        return -1, str(e)
    return 0, ""
