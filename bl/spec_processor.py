import asyncio
import hashlib
from logging import root
import os
from posix import link
import warnings
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.live import Live
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TaskID, TextColumn
from rich.table import Table
from typing_extensions import deprecated

from .spec_parser import ModuleSpec, OriginType, ProjectSpec, RefspecInfo

console = Console()


def rich_warning(message, category, filename, lineno, file=None, line=None):
    console.print(f"[yellow]Warning:[/] {category.__name__}: {message}\n[dim]{filename}:{lineno}[/]")


warnings.showwarning = rich_warning
warnings.simplefilter("default", DeprecationWarning)


english_env = os.environ.copy()
# Ensure git outputs in English for consistent parsing
english_env["LANG"] = "en_US.UTF-8"


# for single branch we should clone shallow but for other we should clone
# with tree:0 filter and because this avoid confusing fetch for git to have the history
# before fetching


def create_clone_args(name: str, ref_spec_info: RefspecInfo, remote_url: str, shallow: bool) -> List[str]:
    """Creates git clone arguments based on the base origin."""
    args = [
        "clone",
        "--filter=tree:0",
        "--sparse",
    ]

    if name == "odoo" or shallow:
        args += [
            "--depth",
            "1",
        ]

    if ref_spec_info.type == OriginType.REF:
        args += [
            "--revision",
            ref_spec_info.refspec,
        ]
    else:
        args += [
            "--origin",
            ref_spec_info.remote,
            "--branch",
            ref_spec_info.refspec,
        ]

    args += [
        remote_url,
    ]

    return args


def normalize_merge_result(ret: int, out: str, err: str):
    if "CONFLICT" in out:
        return -1, out

    return ret, err


def get_local_ref(origin: RefspecInfo) -> str:
    """Generates a local reference name for a given origin."""
    if origin.type == OriginType.PR:
        pr_id = origin.refspec.split("/")[-2]
        return f"pr/{pr_id}"
    else:
        return f"loc-{origin.refspec}"


class SpecProcessor:
    """
    Processes a ProjectSpec by concurrently cloning and merging modules.
    """

    def __init__(self, workdir: Path, concurrency: int = 4):
        self.workdir = workdir
        self.modules_dir = workdir / "external-src"
        self.concurrency = concurrency
        self.semaphore = asyncio.Semaphore(concurrency)

    def get_module_path(self, module_name: str, module_spec: ModuleSpec) -> Path:
        """Returns the path to the module directory."""
        if module_name == "odoo" and module_spec.target_folder is None:
            warnings.warn(
                "importing 'odoo' without a 'target_folder' "
                + "property is deprecated. Use target_folder: 'src/' in spec.yaml.",
                DeprecationWarning,
            )
            return self.workdir / "src/"
        elif module_spec.target_folder is not None:
            return self.workdir / module_spec.target_folder
        else:
            return self.modules_dir / module_name

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
    async def run_shell_commands(self, progress: Progress, task_id: TaskID, spec: ModuleSpec, module_path: Path) -> int:
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

    async def fetch_local_ref(
        self,
        origin: RefspecInfo,
        local_ref: str,
        module_path: Path,
    ) -> tuple[int, str, str]:
        return await self.run_git(
            "fetch",
            origin.remote,
            f"{origin.refspec}:{local_ref}",
            cwd=module_path,
        )

    async def clone_base_repo_ref(
        self, name: str, ref_spec_info: RefspecInfo, remote_url: str, module_path: Path, shallow: bool
    ) -> tuple[int, str, str]:
        args = create_clone_args(name, ref_spec_info, remote_url, shallow)

        ret, out, err = await self.run_git(
            *args,
            str(module_path),
        )

        # if it's a ref we need to manually create a base branch because we cannot
        # merge in a detached head
        if ref_spec_info.type == OriginType.REF:
            ret, out, err = await self.run_git(
                "checkout",
                "-b",
                ref_spec_info.ref_name or ref_spec_info.refspec[:10],
                cwd=str(module_path),
            )

        return ret, out, err

    async def try_merge(
        self,
        progress: Progress,
        task_id: TaskID,
        remote_url: str,
        local_ref: str,
        module_path: Path,
        origin: RefspecInfo,
    ) -> tuple[int, str]:
        # Merge
        # I think the idea would be to not fetch shallow but fetch treeless and do a merge-base
        # then fetch the required data and then merge
        progress.update(task_id, status=f"Merging {local_ref}", advance=0.1)
        ret, out, err = await self.run_git("merge", "--no-edit", local_ref, cwd=module_path)
        ret, err = normalize_merge_result(ret, out, err)

        if "CONFLICT" in err:
            progress.update(task_id, status=f"[red]Merge conflict in {origin.refspec}: {err}")
            # In case of conflict, we might want to abort the merge
            await self.run_git("merge", "--abort", cwd=module_path)
        return ret, err

    async def setup_new_repo(
        self,
        progress: Progress,
        task_id: TaskID,
        spec: ModuleSpec,
        name: str,
        root_refspec_info: RefspecInfo,
        remote_url: str,
        module_path: Path,
    ) -> int:
        progress.update(
            task_id,
            status=(f"Cloning {root_refspec_info.remote}/{root_refspec_info.refspec}"),
        )

        # Clone shallowly with blobless filter and no checkout
        # We don't use the cache yet for simplicity, but we follow the optimized command
        # User --revision for specific commit checkout if needed
        shallow_clone = len(spec.refspec_info) == 1
        ret, out, err = await self.clone_base_repo_ref(name, root_refspec_info, remote_url, module_path, shallow_clone)

        if ret != 0:
            status_message = (
                f"[red]Clone failed {root_refspec_info.remote}({remote_url})/{root_refspec_info.refspec}"
                + f" -> {module_path}:\n{err}"
            )
            progress.update(task_id, status=status_message)
            return ret

    async def reset_repo_for_work(
        self, progress: Progress, task_id: TaskID, spec: ModuleSpec, root_refspec_info: RefspecInfo, module_path: Path
    ) -> int:
        ret, out, err = await self.run_git("status", "--porcelain", cwd=module_path)

        if out != "":
            progress.update(task_id, status=f"[red]Repo is dirty:\n{out}")
            return ret
        # Reset all the local origin to their remote origins
        progress.update(
            task_id,
            status=(f"Resetting existing repository for {root_refspec_info.remote}/{root_refspec_info.refspec}"),
        )

        s_ret, s_out, s_err = await self.run_git("rev-parse", "--is-shallow-repository", cwd=module_path)
        if len(spec.refspec_info) > 1 and s_out == "true":
            await self.run_git("fetch", "--unshallow", cwd=module_path)

        reset_target = f"{root_refspec_info.remote}/{root_refspec_info.refspec}"
        ret, out, err = await self.run_git("reset", "--hard", reset_target, cwd=module_path)
        if ret != 0:
            progress.update(task_id, status=f"[red]Reset failed: {err}")
            return ret

        for refspec_info in spec.refspec_info[1:]:
            local_ref = get_local_ref(refspec_info)
            # This is probably the best thing but for now this works good enough
            # TODO(franz): find something better
            ret, out, err = await self.run_git("branch", "-d", local_ref, cwd=module_path)

    def link_all_modules(self, module_list: List[str], module_path: Path) -> tuple[int, str]:
        links_path = self.workdir / "links"
        links_path.mkdir(exist_ok=True)

        # Remove all symlink

        for module_name in module_list:
            try:
                path_src_symlink = module_path / module_name
                path_dest_symlink = links_path / module_name

                if path_dest_symlink.is_symlink():
                    path_dest_symlink.unlink()

                os.symlink(path_src_symlink, path_dest_symlink, True)
            except OSError as e:
                return -1, str(e)

        return 0, ""

    async def merge_spec_into_tree(
        self,
        progress: Progress,
        task_id: TaskID,
        spec: ModuleSpec,
        refspec_info: RefspecInfo,
        root_refspec_info: RefspecInfo,
        module_path: Path,
    ) -> tuple[int, str]:
        # This is weird...
        remote_url = spec.remotes.get(refspec_info.remote) or refspec_info.remote

        local_ref = get_local_ref(refspec_info)
        remote_ref = refspec_info.refspec

        ret, err = await self.try_merge(progress, task_id, remote_url, local_ref, module_path, refspec_info)
        if ret != 0:
            return ret, err

        progress.advance(task_id)
        return 0, ""

    def get_refspec_by_remote(self, refspec_info_list: List[RefspecInfo]) -> Dict[str, List[RefspecInfo]]:
        result = {}

        for spec in refspec_info_list:
            spec_list = result.get(spec.remote, [])
            spec_list.append(spec)
            result[spec.remote] = spec_list

        return result

    async def fetch_multi(self, remote: str, refspec_info_list: List[RefspecInfo], module_path: Path):
        args = [
            "fetch",
            "-j",
            str(self.concurrency),
            remote,
        ]

        for refspec_info in refspec_info_list:
            local_ref = get_local_ref(refspec_info)
            args += [f"{refspec_info.refspec}:{local_ref}"]

        ret, out, err = await self.run_git(*args, cwd=module_path)

        return ret, out, err

    def filter_non_link_module(self, spec: ModuleSpec):
        result = []
        base_path_links = self.workdir / "links"
        for module in spec.modules:
            path = base_path_links / module
            if path.is_symlink() or not path.exists():
                result.append(module)
            else:
                console.print(
                    f"[purple]Watchout ![/] {module} is not a symlink and will be assumed "
                    + "to be a local module\nIt will not be fetched or linked"
                )
        return result

    async def process_module(
        self, name: str, spec: ModuleSpec, progress: Progress, count_progress: Progress, count_task: TaskID
    ) -> int:
        """Processes a single ModuleSpec."""
        total_steps = len(spec.refspec_info) + 1 if spec.refspec_info else 1

        symlink_modules = self.filter_non_link_module(spec)

        async with self.semaphore:
            task_id = progress.add_task(f"[cyan]{name}", status="Waiting...", total=total_steps)
            try:
                if not spec.refspec_info:
                    progress.update(task_id, status="[yellow]No origins defined", completed=1)
                    return -1

                module_path = self.get_module_path(name, spec)

                # 1. Initialize with first origin
                root_refspec_info = spec.refspec_info[0]
                remote_url = spec.remotes.get(root_refspec_info.remote) or root_refspec_info.remote

                if not module_path.exists() or not module_path.is_dir():
                    await self.setup_new_repo(progress, task_id, spec, name, root_refspec_info, remote_url, module_path)
                else:
                    await self.reset_repo_for_work(progress, task_id, spec, root_refspec_info, module_path)

                if name != "odoo":
                    # We don't do sparse checkout for odoo because the odoo repo does not work at
                    # all like the other repos (modules are in addons/ and src/addons/) instead of
                    # at the root of the repo

                    # TODO(franz): there is probably a way to make it work, but for now we skip it
                    # this is probably a good way to gain performance

                    # 2. Sparse Checkout setup
                    progress.update(task_id, status="Configuring sparse checkout...")
                    await self.run_git("sparse-checkout", "init", "--cone", cwd=module_path)
                    if symlink_modules:
                        await self.run_git("sparse-checkout", "set", *spec.modules, cwd=module_path)

                checkout_target = root_refspec_info.refspec

                await self.run_git("checkout", checkout_target, cwd=module_path)
                progress.advance(task_id)

                for remote, remote_url in spec.remotes.items():
                    await self.run_git("remote", "add", remote, remote_url, cwd=module_path)
                    await self.run_git("config", f"remote.{remote}.partialCloneFilter", "tree:0", cwd=module_path)
                    await self.run_git("config", f"remote.{remote}.promisor", "true", cwd=module_path)

                # TODO(franz) fetch and merge should be done separately
                # fetch can be done in parallel by git with -j X and putting several refspec as parameters
                # to git fetch
                refspec_by_remote: Dict[str, List[RefspecInfo]] = self.get_refspec_by_remote(spec.refspec_info[1:])

                for remote, refspec_list in refspec_by_remote.items():
                    progress.update(task_id, status=f"Fetching multi from {remote}")
                    await self.fetch_multi(remote, refspec_list, module_path)

                # 4. Fetch and Merge remaining origins
                for refspec_info in spec.refspec_info[1:]:
                    ret, err = await self.merge_spec_into_tree(
                        progress, task_id, spec, refspec_info, root_refspec_info, module_path
                    )
                    if ret != 0:
                        progress.update(task_id, status=f"[purple]Merge failed from {refspec_info.refspec}: {err}")
                        return -1

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

                progress.update(task_id, status="Linking directory")
                ret, err = self.link_all_modules(symlink_modules, module_path)
                if ret != 0:
                    progress.update(task_id, status=f"[red]Could not link modules: {err}")
                    return ret

                progress.update(task_id, status="[green]Complete")
                progress.remove_task(task_id)
                count_progress.advance(count_task)

            except Exception as e:
                progress.update(task_id, status=f"[red]Error: {str(e)}")
                return -1

        return 0

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

            # this should error if a task crashes
            return_codes = await asyncio.gather(*tasks)
            if any(return_codes):
                raise Exception()


async def process_project(project_spec: ProjectSpec, concurrency: int = 4) -> None:
    """Helper function to run the SpecProcessor."""
    processor = SpecProcessor(project_spec.workdir, concurrency)
    # project_spec.specs = {name: spec for name, spec in project_spec.specs.items() if name == "sale-workflow"}
    return await processor.process_project(project_spec)
