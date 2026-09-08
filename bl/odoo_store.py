"""Shared, managed Odoo worktrees, prepared from ordered refs and patch contents."""

import asyncio
import fcntl
import glob
import hashlib
import json
import logging
import os
import shlex
import tempfile
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from bl.config import get_odoo_store_root
from bl.odoo_types import OdooCheckoutSelection, OdooProjectBinding, OdooSourceRef, OdooWorktreeRecipe, content_id
from bl.types import OriginType, RepoInfo
from bl.utils import run_git

logger = logging.getLogger(__name__)


class OdooStoreError(RuntimeError):
    pass


def can_share_odoo(spec: RepoInfo) -> bool:
    return bool(spec.refspec_info) and not spec.paths


def managed_odoo_root(path: Path) -> Path | None:
    """Recognize our worktrees even if the configured store has since moved."""
    git_file = path / ".git"
    if not git_file.is_file():
        return None
    value = git_file.read_text().strip()
    if not value.startswith("gitdir: "):
        return None
    admin = (path / value[len("gitdir: ") :]).resolve()
    repository = admin.parent.parent
    if (repository / "bl-odoo-managed").is_file():
        return repository.parent.parent
    return None


async def read_shared_odoo_refs(path: Path) -> list[str] | None:
    root = managed_odoo_root(path)
    if root is None:
        return None
    common = Path((await _git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=path)).strip())
    async with store_lock(root, common.stem):
        record = json.loads((root / "metadata" / f"{path.resolve().name}.json").read_text())
        head = (await _git("rev-parse", "HEAD", cwd=path)).strip()
        if head != record["head_sha"]:
            raise OdooStoreError("Odoo checkout and build metadata differ; rebuild before freezing")
        return record.get("resolved_refs", [head])


@asynccontextmanager
async def store_lock(root: Path, name: str):
    """Nonblocking acquisition keeps the event loop available to other builds."""
    directory = root / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{name}.lock").open("a") as stream:
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


async def _git(*args: str, cwd: Path, bare: bool = False) -> str:
    ret, out, err = await run_git(*args, cwd=cwd, git_dir=cwd if bare else None)
    if ret:
        raise OdooStoreError(f"Odoo Git {args[0]} failed in {cwd}: {err.strip()}")
    return out


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}")
    try:
        temporary.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _source_url(url: str, workdir: Path) -> str:
    parsed = urlsplit(url)
    if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
        return Path(unquote(parsed.path)).resolve().as_uri()
    if not parsed.scheme and ":" not in url:
        return (workdir / url).resolve().as_uri()
    return url.rstrip("/")


async def _protect_files(path: Path) -> None:
    files = (await _git("ls-files", "-z", cwd=path)).split("\0")
    for name in filter(None, files):
        file = path / name
        if not file.is_symlink() and file.is_file():
            file.chmod(file.stat().st_mode & ~0o222)


async def _assert_clean(path: Path) -> None:
    if not (path / ".git").exists():
        raise OdooStoreError(f"Odoo source is not a repository: {path}")
    if (await _git("status", "--porcelain", "--untracked-files=all", cwd=path)).strip():
        raise OdooStoreError(f"Odoo source has local changes; leaving it untouched: {path}")


async def _prepare(
    repository: Path, path: Path, resolved: list[str], patches: tuple[bytes, ...], selection: OdooCheckoutSelection
) -> str:
    await _git("worktree", "add", "--detach", "--no-checkout", str(path), resolved[0], cwd=repository, bare=True)
    await _git("config", "--worktree", "core.bare", "false", cwd=path)
    if len(resolved) > 1 or patches:
        # Transforms can touch files outside the final sparse selection. Build
        # the complete tree privately, then apply the consumer's selection.
        await _git("reset", "--hard", resolved[0], cwd=path)
        commit_options = (
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=BL",
            "-c",
            "user.email=bl@localhost",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "merge.gpgSign=false",
        )
        for sha in resolved[1:]:
            await _git(*commit_options, "merge", "--no-edit", "--no-verify", "--ff", sha, cwd=path)
        # Use the bytes whose digests were used in the recipe, never re-read
        # mutable project patch files during a build or on another consumer.
        admin = Path((await _git("rev-parse", "--absolute-git-dir", cwd=path)).strip())
        with tempfile.TemporaryDirectory(prefix="patches-", dir=admin) as directory:
            for index, patch in enumerate(patches):
                patch_file = Path(directory) / f"{index:06d}.patch"
                patch_file.write_bytes(patch)
                ret, _, _ = await run_git("apply", "--reverse", "--check", str(patch_file), cwd=path)
                if ret:
                    await _git(*commit_options, "am", "--committer-date-is-author-date", str(patch_file), cwd=path)
    mode, patterns = selection.sparse_parameters()
    await _git("sparse-checkout", "set", mode, "--", *patterns, cwd=path)
    await _git("reset", "--hard", "HEAD", cwd=path)
    await _protect_files(path)
    return (await _git("rev-parse", "HEAD", cwd=path)).strip()


def _read_patches(spec: RepoInfo, target: Path) -> tuple[bytes, ...]:
    patterns = []
    for command in spec.shell_commands:
        parts = shlex.split(command)
        if len(parts) < 3 or parts[:2] != ["git", "am"] or any(p.startswith("-") for p in parts[2:]):
            raise OdooStoreError("Odoo shell commands cannot be shared; use patch_globs for patches")
        # Only the legacy declarative git-am form is supported; no shell runs.
        patterns.extend(parts[2:])
    patterns.extend(spec.patch_globs_to_apply)
    patches = []
    for pattern in patterns:
        # Normalize '..' lexically before globbing: src may already be a symlink
        # into the global store, but patch paths belong to the project.
        absolute_pattern = os.path.normpath(str(target / pattern))
        matches = sorted(glob.glob(absolute_pattern, recursive=True))
        if not matches:
            raise OdooStoreError(f"Odoo patch pattern has no matches: {pattern}")
        for match in matches:
            path = Path(match)
            if not path.is_file():
                raise OdooStoreError(f"Odoo patch is not a file: {path}")
            patches.append(path.read_bytes())
    return tuple(patches)


async def _fetch_ref(repository: Path, source: OdooSourceRef, *, full_history: bool, base: bool) -> str:
    remote = "origin" if base else f"source-{content_id(source.url)}"
    for key, value in (("url", source.url), ("promisor", "true"), ("partialclonefilter", "blob:none")):
        await _git("config", f"remote.{remote}.{key}", value, cwd=repository, bare=True)
    options = [] if full_history else ["--depth=1"]
    if (
        full_history
        and (await _git("rev-parse", "--is-shallow-repository", cwd=repository, bare=True)).strip() == "true"
    ):
        options = ["--unshallow"]
    if source.kind == "ref":
        if len(source.ref) not in (40, 64) or any(c not in "0123456789abcdef" for c in source.ref):
            raise OdooStoreError("Odoo frozen revision must be a full commit SHA")
        destination = f"refs/bl/pins/{source.ref}"
        ret, _, _ = await run_git("cat-file", "-e", f"{source.ref}^{{commit}}", cwd=repository, git_dir=repository)
        if ret or options == ["--unshallow"]:
            await _git("fetch", *options, "--filter=blob:none", remote, source.ref, cwd=repository, bare=True)
        await _git("update-ref", destination, source.ref, cwd=repository, bare=True)
    else:
        requested = source.ref if source.kind == "pr" else f"refs/heads/{source.ref}"
        await _git("check-ref-format", requested, cwd=repository, bare=True)
        destination = (
            f"refs/remotes/origin/{source.ref}"
            if base and source.kind == "branch"
            else f"refs/bl/sources/{content_id((source.url, source.ref, source.kind))}"
        )
        await _git(
            "fetch", *options, "--filter=blob:none", remote, f"+{requested}:{destination}", cwd=repository, bare=True
        )
    return (await _git("rev-parse", f"{destination}^{{commit}}", cwd=repository, bare=True)).strip()


async def _remove_worktree(repository: Path, path: Path) -> None:
    # Used only for our unpublished staging directory, or an explicitly pruned
    # clean worktree. Never point this at a project checkout.
    ret, _, err = await run_git("worktree", "remove", "--force", str(path), cwd=repository, git_dir=repository)
    if ret and path.exists():
        raise OdooStoreError(f"Cannot remove Odoo worktree {path}: {err.strip()}")
    await _git("worktree", "prune", cwd=repository, bare=True)


async def _attach(target: Path, source: Path, root: Path, recipe: OdooWorktreeRecipe) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.exists() and not target.is_symlink():
        if any(target.iterdir()):
            await _assert_clean(target)
            # Retain a real clone, including local history and ignored files.
            # Migration must not discard commits merely because status is clean.
            backup = target.with_name(f"{target.name}.bl-backup-{uuid4().hex[:8]}")
            target.rename(backup)
            logger.warning("Original Odoo checkout retained at %s", backup)
        else:
            target.rmdir()
    temporary = target.with_name(f".{target.name}.{uuid4().hex}")
    binding = OdooProjectBinding(str(target), recipe.repository_id, recipe.worktree_id)
    try:
        temporary.symlink_to(source, target_is_directory=True)
        # Register before publication so a concurrent prune cannot miss a new
        # consumer. The repository lock is held throughout this operation.
        _write_json(root / "bindings" / f"{binding.binding_id}.json", asdict(binding))
        temporary.replace(target)
    except BaseException:
        if backup is not None and not target.exists():
            backup.rename(target)
        raise
    finally:
        temporary.unlink(missing_ok=True)


async def build_shared_odoo(spec: RepoInfo, target: Path, workdir: Path) -> None:
    root = get_odoo_store_root()
    # Resolve parents, not the final project-side symlink.
    target = target.parent.resolve() / target.name
    if target.name == ".." or workdir.resolve().is_relative_to(target):
        raise OdooStoreError("Odoo target_folder must not contain the project directory itself")
    inputs = tuple(
        OdooSourceRef(_source_url(spec.remotes[ref.remote], workdir), ref.refspec.strip(), ref.type.value)
        for ref in spec.refspec_info
    )
    ref = spec.refspec_info[0]
    url = inputs[0].url
    patches = _read_patches(spec, target)
    recipe = OdooWorktreeRecipe(
        repository_id=content_id(url),
        ref=ref.refspec.strip(),
        pinned=ref.type == OriginType.REF,
        selection=OdooCheckoutSelection(tuple(sorted(set(spec.modules))), tuple(sorted(set(spec.locales)))),
        merges=inputs[1:],
        patch_digests=tuple(hashlib.sha256(patch).hexdigest() for patch in patches),
        ref_kind="pr" if ref.type == OriginType.PR else "",
    )
    repository = root / "repositories" / f"{recipe.repository_id}.git"
    source = root / "worktrees" / recipe.repository_id / recipe.worktree_id
    record_path = root / "metadata" / f"{recipe.worktree_id}.json"
    async with store_lock(root, f"project-{content_id(str(target))}"):
        if target.exists() and not target.is_symlink() and any(target.iterdir()):
            await _assert_clean(target)
            if (target / ".git").is_file():
                raise OdooStoreError(
                    "Odoo source is already a linked worktree; relocate it with git worktree move before rebuilding"
                )
        async with store_lock(root, recipe.repository_id):
            repository.mkdir(parents=True, exist_ok=True)
            if not (repository / "HEAD").exists():
                await _git("init", "--bare", str(repository), cwd=repository, bare=True)
            await _git("config", "extensions.worktreeConfig", "true", cwd=repository, bare=True)
            await _git("config", "remote.origin.url", url, cwd=repository, bare=True)
            await _git("config", "remote.origin.promisor", "true", cwd=repository, bare=True)
            await _git("config", "remote.origin.partialclonefilter", "blob:none", cwd=repository, bare=True)
            (repository / "bl-odoo-managed").write_text("1\n")
            hook = repository / "hooks" / "pre-commit"
            hook.parent.mkdir(exist_ok=True)
            hook.write_text('#!/bin/sh\necho "Odoo is shared and cannot be edited." >&2\nexit 1\n')
            hook.chmod(0o755)

            # A killed build can leave a registered but unpublished checkout.
            # Holding the repository lock proves no other build is preparing it.
            for abandoned in source.parent.glob(".preparing-*"):
                await _remove_worktree(repository, abandoned)

            # Once merge histories are needed, never shallow this repository
            # again: another project's simple build must not truncate them.
            if recipe.merges:
                await _git("config", "bl.odooFullHistory", "true", cwd=repository, bare=True)
            _, full, _ = await run_git("config", "--get", "bl.odooFullHistory", cwd=repository, git_dir=repository)
            resolved = []
            for index, item in enumerate(inputs):
                resolved.append(
                    await _fetch_ref(repository, item, full_history=full.strip() == "true", base=index == 0)
                )
            source.parent.mkdir(parents=True, exist_ok=True)
            if source.exists():
                await _assert_clean(source)
                old_sha = (await _git("rev-parse", "HEAD", cwd=source)).strip()
            else:
                old_sha = None

            previous = json.loads(record_path.read_text()) if record_path.exists() else {}
            previous_refs = previous.get("resolved_refs", [previous.get("head_sha")])
            sha = old_sha
            if old_sha is None or previous_refs != resolved or previous.get("head_sha") != old_sha:
                staging = source.with_name(f".preparing-{uuid4().hex}")
                try:
                    # Materialize all requested blobs before touching live files.
                    sha = await _prepare(repository, staging, resolved, patches, recipe.selection)
                    if old_sha is None:
                        await _git("worktree", "move", str(staging), str(source), cwd=repository, bare=True)
                    else:
                        try:
                            await _git("reset", "--hard", sha, cwd=source)
                        except BaseException:
                            await _git("reset", "--hard", old_sha, cwd=source)
                            raise
                        finally:
                            await _protect_files(source)
                finally:
                    if staging.exists():
                        await _remove_worktree(repository, staging)
            await _protect_files(source)
            _write_json(record_path, {"recipe": asdict(recipe), "head_sha": sha, "resolved_refs": resolved})
            await _attach(target, source, root, recipe)


async def prune_unused_worktrees(root: Path, *, dry_run: bool = True) -> list[Path]:
    """Explicit cleanup of unreferenced published worktrees; no automatic GC."""
    root = root.resolve()
    candidates = []
    for record_path in (root / "metadata").glob("*.json"):
        try:
            record = json.loads(record_path.read_text())
        except FileNotFoundError:
            continue  # Another cleanup already removed this record.
        repository_id = record["recipe"]["repository_id"]
        async with store_lock(root, repository_id):
            source = root / "worktrees" / repository_id / record_path.stem
            used = False
            stale = []
            for file in (root / "bindings").glob("*.json"):
                binding = json.loads(file.read_text())
                target = Path(binding["target_path"])
                if target.is_symlink() and target.resolve() == source:
                    used = True
                elif binding["worktree_id"] == record_path.stem:
                    stale.append(file)
            if used:
                continue
            repository = root / "repositories" / f"{repository_id}.git"
            if source.exists():
                await _assert_clean(source)
            candidates.append(source)
            if dry_run:
                continue
            if source.exists():
                await _remove_worktree(repository, source)
            for file in stale:
                file.unlink(missing_ok=True)
            record_path.unlink(missing_ok=True)
    return candidates
