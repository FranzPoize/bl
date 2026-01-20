import asyncio
import os
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Any
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskID
from rich.live import Live
from rich.console import Console
from .spec_parser import ProjectSpec, ModuleSpec, ModuleOrigin, OriginType

console = Console()


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
            "--single-branch",
            "--branch",
            base_origin.origin,
        ]

    args += [
        remote_url,
    ]

    return args


class SpecProcessor:
    """
    Processes a ProjectSpec by concurrently cloning and merging modules.
    """

    def __init__(self, workdir: Path, concurrency: int = 4):
        self.workdir = workdir
        self.modules_dir = workdir / "modules"
        self.cache_dir = workdir / "_cache"
        self.concurrency = concurrency
        self.semaphore = asyncio.Semaphore(concurrency)

    def _get_cache_path(self, remote_url: str) -> Path:
        """Returns a unique cache path for a given remote URL."""
        url_hash = hashlib.sha256(remote_url.encode()).hexdigest()[:12]
        return self.cache_dir / url_hash

    async def run_git(self, *args: str, cwd: Optional[Path] = None) -> tuple[int, str, str]:
        """Executes a git command asynchronously."""
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
        )
        stdout, stderr = await proc.communicate()
        returncode = proc.returncode if proc.returncode is not None else -1
        return returncode, stdout.decode().strip(), stderr.decode().strip()

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
        for i in range(4):
            ret, out, err = await self.run_git("merge", "--no-edit", local_ref, cwd=module_path)
            if ret != 0:
                await self.run_git("merge", "--abort", cwd=module_path)
                ret, out, err = await self.run_git(
                    "fetch",
                    "--deepen",
                    str(100 ** (i + 1)),
                    remote_url,
                    f"{origin.origin}:{local_ref}",
                    cwd=module_path,
                )
                progress.update(task_id, status=f"Merging {local_ref} into {base_origin.origin} (attempt {i + 2})...")
                if ret != 0:
                    progress.update(task_id, status=f"[red]Deepen fetch failed: {err}")
                    return ret, out, err
                progress.update(task_id, status=f"[yellow]Deepening fetch to depth {100 ** (i + 1)}...")
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
            progress.update(task_id, status=f"[red]Deepen fetch failed while merging {local_ref}: {err}")
            return ret, out, err
        ret, out, err = await self.run_git("merge", "--no-edit", local_ref, cwd=module_path)

        if ret != 0:
            progress.update(task_id, status=f"[red]Merge conflict in {origin.origin}: {err}")
            # In case of conflict, we might want to abort the merge
            await self.run_git("merge", "--abort", cwd=module_path)

        return ret, out, err

    async def process_module(self, name: str, spec: ModuleSpec, progress: Progress) -> None:
        """Processes a single ModuleSpec."""
        total_steps = len(spec.origins) if spec.origins else 1

        async with self.semaphore:
            task_id = progress.add_task(f"[cyan]{name}", status="Waiting...", total=total_steps)
            try:
                module_path = self.modules_dir / name
                if module_path.exists():
                    # For now, we clear the existing directory to ensure a clean state
                    # In a real scenario, we might want to update it
                    import shutil

                    shutil.rmtree(module_path)

                module_path.mkdir(parents=True, exist_ok=True)

                if not spec.origins:
                    progress.update(task_id, status="[yellow]No origins defined", completed=1)
                    return

                # 1. Initialize with first origin
                base_origin = spec.origins[0]
                remote_url = (spec.remotes or {}).get(base_origin.remote) or base_origin.remote

                progress.update(task_id, status=f"Cloning {base_origin.origin}...")

                # Clone shallowly with blobless filter and no checkout
                # We don't use the cache yet for simplicity, but we follow the optimized command
                # User --revision for specific commit checkout if needed
                args = create_clone_args(base_origin, remote_url)

                ret, out, err = await self.run_git(
                    *args,
                    str(module_path),
                )

                if ret != 0:
                    progress.update(task_id, status=f"[red]Clone failed: {err}")
                    return

                # 2. Sparse Checkout setup
                progress.update(task_id, status="Configuring sparse checkout...")
                await self.run_git("sparse-checkout", "init", "--cone", cwd=module_path)
                if spec.modules:
                    await self.run_git("sparse-checkout", "set", *spec.modules, cwd=module_path)

                # 3. Checkout base
                await self.run_git("checkout", base_origin.origin, cwd=module_path)
                progress.advance(task_id)

                # 4. Fetch and Merge remaining origins
                for i, origin in enumerate(spec.origins[1:], 1):
                    progress.update(
                        task_id,
                        status=(
                            f"Merging {origin.remote}/{origin.origin}"
                            + f" into {base_origin.remote}/{base_origin.origin}..."
                        ),
                    )

                    remote_url = (spec.remotes or {}).get(origin.remote) or origin.remote

                    if origin.type == OriginType.PR:
                        # Fetch PR ref
                        # Pattern: refs/pull/{id}/head
                        pr_id = origin.origin.split("/")[-2]
                        local_ref = f"pr/{pr_id}"
                        ret, out, err = await self.run_git(
                            "fetch", remote_url, f"{origin.origin}:{local_ref}", cwd=module_path
                        )
                    else:
                        # Fetch Branch or commit hash
                        local_ref = f"merge-{i}"
                        ret, out, err = await self.run_git(
                            "fetch", "--depth", "1", remote_url, f"{origin.origin}:{local_ref}", cwd=module_path
                        )

                    if ret != 0:
                        progress.update(task_id, status=f"[red]Fetch failed for {origin.origin}")
                        return

                    # Try to merge
                    ret, out, err = await self.try_merge(
                        progress, task_id, remote_url, local_ref, module_path, origin, base_origin
                    )
                    if ret != 0:
                        return

                    progress.advance(task_id)

                progress.update(task_id, status="[green]Complete")
                progress.remove_task(task_id)

            except Exception as e:
                progress.update(task_id, status=f"[red]Error: {str(e)}")

    async def process_project(self, project_spec: ProjectSpec) -> None:
        """Processes all modules in a ProjectSpec."""
        self.modules_dir.mkdir(parents=True, exist_ok=True)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.fields[status]}"),
            console=console,
            refresh_per_second=10,
        ) as progress:
            tasks = []
            for name, spec in project_spec.specs.items():
                tasks.append(self.process_module(name, spec, progress))

            await asyncio.gather(*tasks)


async def process_project(project_spec: ProjectSpec, workdir: Path, concurrency: int = 4) -> None:
    """Helper function to run the SpecProcessor."""
    processor = SpecProcessor(workdir, concurrency)
    # project_spec.specs = {name: spec for name, spec in project_spec.specs.items() if name == "sale-workflow"}
    await processor.process_project(project_spec)
