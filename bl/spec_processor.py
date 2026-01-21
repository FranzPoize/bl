import asyncio
import os
import hashlib
import warnings
from pathlib import Path
from typing import List, Dict, Optional, Any
from typing_extensions import deprecated
from rich.progress import MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, BarColumn, TaskID
from rich.live import Live
from rich.table import Table
from rich.console import Console
from .spec_parser import ProjectSpec, ModuleSpec, ModuleOrigin, OriginType

BASE_DEPTH_VALUE = 10000

console = Console()


def rich_warning(message, category, filename, lineno, file=None, line=None):
    console.print(f"[yellow]Warning:[/] {category.__name__}: {message}\n[dim]{filename}:{lineno}[/]")


warnings.showwarning = rich_warning
warnings.simplefilter("default", DeprecationWarning)


english_env = os.environ.copy()
# Ensure git outputs in English for consistent parsing
english_env["LANG"] = "en_US.UTF-8"


def create_clone_args(base_origin: ModuleOrigin, remote_url: str) -> List[str]:
    """Creates git clone arguments based on the base origin."""
    args = [
        "clone",
        "--no-checkout",
        "--filter=blob:none",
        "--depth",
        "1",
    ]

    if base_origin.type == OriginType.REF:
        args += [
            "--revision",
            base_origin.origin,
        ]
    else:
        args += [
            "--origin",
            base_origin.remote,
            "--single-branch",
            "--branch",
            base_origin.origin,
        ]

    args += [
        remote_url,
    ]

    return args


def _get_local_ref(origin: ModuleOrigin) -> str:
    """Generates a local reference name for a given origin."""
    if origin.type == OriginType.PR:
        pr_id = origin.origin.split("/")[-2]
        return f"pr/{pr_id}"
    else:
        return f"loc-{origin.origin}"


class SpecProcessor:
    """
    Processes a ProjectSpec by concurrently cloning and merging modules.
    """

    def __init__(self, workdir: Path, concurrency: int = 4):
        self.workdir = workdir
        self.modules_dir = workdir / "external-src"
        self.cache_dir = workdir / "_cache"
        self.concurrency = concurrency
        self.semaphore = asyncio.Semaphore(concurrency)

    def _get_cache_path(self, remote_url: str) -> Path:
        """Returns a unique cache path for a given remote URL."""
        url_hash = hashlib.sha256(remote_url.encode()).hexdigest()[:12]
        return self.cache_dir / url_hash

    def get_module_path(self, module_name: str, module_spec: ModuleSpec) -> Path:
        """Returns the path to the module directory."""
        if module_name == "odoo" and module_spec.target_folder is None:
            console.print(
                "[yellow]Warning:[/] importing 'odoo' without a target_folder "
                + "property is deprecated. Use target_folder: 'src/' in spec.yaml."
            )
            return self.workdir / "src/"
        else:
            return self.workdir / "external-src" / module_name

    async def run_git(self, *args: str, cwd: Optional[Path] = None) -> tuple[int, str, str]:
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

    @deprecated(
        "run_shell_commands is deprecated if used to apply patches. Use patch_globs properties in spec.yaml instead."
    )
    async def run_shell_commands(
        self, progress: Progress, task_id: TaskID, spec: ModuleSpec, module_path: Path
    ) -> None:
        for cmd in spec.shell_commands:
            progress.update(task_id, status=f"Running shell command: {cmd}...")
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(module_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=english_env,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                # This is a sanity check because people usually put "git am" commands
                # in shell_commands, so we abort any ongoing git am
                await self.run_git("am", "--abort", cwd=str(module_path))
                progress.update(
                    task_id,
                    status=f"[red]Shell command failed: {cmd}\nError: {stderr.decode().strip()}",
                )
                return -1
        return 0

    async def deepen_fetch(
        self,
        remote_url: str,
        origin: str,
        local_ref: str,
        module_path: Path,
        depth: str,
    ) -> tuple[int, str, str]:
        await self.run_git(
            "fetch",
            "--deepen",
            depth,
            remote_url,
            f"{origin}:{local_ref}",
            cwd=module_path,
        )

    async def try_merge(
        self,
        progress: Progress,
        task_id: TaskID,
        remote_url: str,
        local_ref: str,
        module_path: Path,
        origin: ModuleOrigin,
        base_origin: ModuleOrigin,
    ) -> bool:
        # Merge
        for i in range(2):
            progress.update(
                task_id, status=f"Merging {local_ref} into {base_origin.origin} (attempt {i + 2})...", advance=0.1
            )
            ret, out, err = await self.run_git("merge", "--no-edit", local_ref, cwd=module_path)

            if "CONFLICT" in out:
                progress.update(task_id, status=f"[purple]Merge conflict while merging {origin.origin}")
                return ret, out, err

            if ret != 0:
                await self.run_git("merge", "--abort", cwd=module_path)

                depth = str(BASE_DEPTH_VALUE ** (i + 1))
                progress.update(task_id, status=f"[yellow]Deepening fetch to depth {depth}...")
                fetch_origin = origin.origin
                await self.deepen_fetch(
                    remote_url,
                    fetch_origin,
                    local_ref,
                    module_path,
                    depth,
                )
            else:
                return ret, out, err

        ret, out, err = await self.run_git(
            "fetch",
            "--unshallow",
            remote_url,
            f"{origin.origin}:{local_ref}",
            cwd=module_path,
        )
        if ret != 0:
            progress.update(task_id, status=f"[red]epen fetch failed while merging {local_ref}: {err}")
            return ret, out, err

        ret, out, err = await self.run_git("merge", "--no-edit", local_ref, cwd=module_path)
        if ret != 0:
            progress.update(task_id, status=f"[red]Merge conflict in {origin.origin}: {err}")
            # In case of conflict, we might want to abort the merge
            await self.run_git("merge", "--abort", cwd=module_path)

        return ret, out, err

    async def process_module(
        self, name: str, spec: ModuleSpec, progress: Progress, count_progress: Progress, count_task: TaskID
    ) -> None:
        """Processes a single ModuleSpec."""
        total_steps = len(spec.origins) if spec.origins else 1

        async with self.semaphore:
            task_id = progress.add_task(f"[cyan]{name}", status="Waiting...", total=total_steps)
            try:
                if not spec.origins:
                    progress.update(task_id, status="[yellow]No origins defined", completed=1)
                    return -1

                module_path = self.get_module_path(name, spec)

                # 1. Initialize with first origin
                base_origin = spec.origins[0]
                remote_url = (spec.remotes or {}).get(base_origin.remote) or base_origin.remote

                if not module_path.exists() or not module_path.is_dir():
                    progress.update(task_id, status=f"Cloning {base_origin.origin}...")

                    # Clone shallowly with blobless filter and no checkout
                    # We don't use the cache yet for simplicity, but we follow the optimized command
                    # User --revision for specific commit checkout if needed
                    if name == "odoo":
                        ret, out, err = await self.run_git(
                            "clone",
                            "--filter=blob:none",
                            "--depth",
                            "1",
                            "--origin",
                            base_origin.remote,
                            "--single-branch",
                            "--branch",
                            base_origin.origin,
                            remote_url,
                            module_path,
                        )
                    else:
                        args = create_clone_args(base_origin, remote_url)

                        ret, out, err = await self.run_git(
                            *args,
                            str(module_path),
                        )

                    for name, url in (spec.remotes or {}).items():
                        if name != "origin":
                            await self.run_git("remote", "add", name, url, cwd=module_path)
                            await self.run_git("config", f"remote.{name}.promisor", "true", cwd=module_path)
                            await self.run_git(
                                "config", f"remote.{name}.partialclonefilter", "blob:none", cwd=module_path
                            )

                    if ret != 0:
                        progress.update(task_id, status=f"[red]Clone failed why cloning base branch: {err}")
                        return ret
                else:
                    ret, out, err = await self.run_git("status", "--porcelain", cwd=module_path)

                    if out != "":
                        progress.update(task_id, status=f"[red]Repo is dirty:\n{out}")
                        return ret
                    # Reset all the local origin to their remote origins
                    progress.update(
                        task_id,
                        status=(
                            f"Resetting existing repository for {base_origin.origin}"
                            + " to {base_origin.remote}/{base_origin.origin}..."
                        ),
                    )
                    ret, out, err = await self.run_git(
                        "reset",
                        "--hard",
                        f"{base_origin.remote}/{base_origin.origin}",
                        cwd=module_path,
                    )
                    if ret != 0:
                        progress.update(task_id, status=f"[red]Reset failed: {err}")
                        return ret

                    for origin in spec.origins[1:]:
                        local_ref = _get_local_ref(origin)
                        # This is probably the best thing but for now this works good enough
                        # TODO(franz): find something better
                        ret, out, err = await self.run_git("branch", "-d", local_ref, cwd=module_path)

                if name != "odoo":
                    # We don't do sparse checkout for odoo because the odoo repo does not work at
                    # all like the other repos (modules are in addons/ and src/addons/) instead of
                    # at the root of the repo

                    # TODO(franz): there is probably a way to make it work, but for now we skip it
                    # this is probably a good way to gain performance

                    # 2. Sparse Checkout setup
                    progress.update(task_id, status="Configuring sparse checkout...")
                    await self.run_git("sparse-checkout", "init", "--cone", cwd=module_path)
                    if spec.modules:
                        await self.run_git("sparse-checkout", "set", *spec.modules, cwd=module_path)

                # 3. Checkout base
                await self.run_git("checkout", base_origin.origin, cwd=module_path)
                progress.advance(task_id)

                # 4. Fetch and Merge remaining origins
                for origin in spec.origins[1:]:
                    progress.update(
                        task_id,
                        status=(
                            f"Merging {origin.remote}/{origin.origin}"
                            + f" into {base_origin.remote}/{base_origin.origin}..."
                        ),
                        advance=0.1,
                    )

                    remote_url = (spec.remotes or {}).get(origin.remote) or origin.remote

                    local_ref = _get_local_ref(origin)

                    ret, out, err = await self.run_git(
                        "fetch", "--depth", "1", remote_url, f"{origin.origin}:{local_ref}", cwd=module_path
                    )

                    if ret != 0:
                        # Should not necessarily crash
                        progress.update(task_id, status=f"[red]Fetch failed for {origin.origin}")
                        return ret

                    # Try to merge
                    ret, out, err = await self.try_merge(
                        progress, task_id, remote_url, local_ref, module_path, origin, base_origin
                    )
                    if ret != 0:
                        return ret

                    progress.advance(task_id)

                if spec.shell_commands:
                    ret = await self.run_shell_commands(progress, task_id, spec, module_path)
                    if ret != 0:
                        return ret

                if spec.patch_globs_to_apply:
                    for glob in spec.patch_globs_to_apply:
                        progress.update(task_id, status=f"Applying patches: {glob}...", advance=0.1)
                        ret, out, err = await self.run_git("am", glob, cwd=module_path)
                        if ret != 0:
                            await self.run_git("am", "--abort", cwd=module_path)
                            progress.update(task_id, status=f"[red]Applying patches failed: {err}")
                            return ret

                progress.update(task_id, status="[green]Complete")
                progress.remove_task(task_id)
                count_progress.advance(count_task)

            except Exception as e:
                progress.update(task_id, status=f"[red]Error: {str(e)}")

    async def process_project(self, project_spec: ProjectSpec) -> None:
        """Processes all modules in a ProjectSpec."""
        self.modules_dir.mkdir(parents=True, exist_ok=True)

        task_list_progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.fields[status]}"),
        )

        task_count_progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
        )
        count_task = task_count_progress.add_task("Processing Modules", total=len(project_spec.specs))

        progress_table = Table.grid()
        progress_table.add_row(
            task_list_progress,
        )
        progress_table.add_row(
            task_count_progress,
        )

        with Live(progress_table, console=console, refresh_per_second=10):
            tasks = []
            for name, spec in project_spec.specs.items():
                tasks.append(
                    self.process_module(
                        name,
                        spec,
                        task_list_progress,
                        task_count_progress,
                        count_task,
                    )
                )

            await asyncio.gather(*tasks)


async def process_project(project_spec: ProjectSpec, workdir: Path, concurrency: int = 4) -> None:
    """Helper function to run the SpecProcessor."""
    processor = SpecProcessor(workdir, concurrency)
    # project_spec.specs = {name: spec for name, spec in project_spec.specs.items() if name == "sale-workflow"}
    await processor.process_project(project_spec)
