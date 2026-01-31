import asyncio
import os
import warnings
from pathlib import Path
from typing import Dict, List

from rich.console import Console
from rich.live import Live
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TaskID, TextColumn
from rich.table import Column, Table
from typing_extensions import deprecated

from bl.utils import english_env, get_local_ref, get_module_path, run_git

from bl.types import CloneFlags, CloneInfo, OriginType, ProjectSpec, RefspecInfo, RepoInfo

console = Console()

# TODO(franz): it's a bit better now but better keep an eye on it


def rich_warning(message, category, filename, lineno, file=None, line=None):
    console.print(f"[yellow]Warning:[/] {category.__name__}: {message}\n[dim]{filename}:{lineno}[/]")


warnings.showwarning = rich_warning
warnings.simplefilter("default", DeprecationWarning)


def check_path_is_repo(module_path: Path):
    # TODO(franz): add check for .git folder
    return not module_path.exists() or not module_path.is_dir()


def clone_info_from_repo(name: str, repo_info: RepoInfo):
    flags = CloneFlags.SHALLOW if name == "odoo" or len(repo_info.refspec_info) == 1 else 0
    flags |= CloneFlags.SPARSE if name != "odoo" or len(repo_info.locales) > 0 else 0
    root_refspec_info = repo_info.refspec_info[0]
    remote_url = repo_info.remotes.get(root_refspec_info.remote)

    return CloneInfo(
        remote_url,
        flags,
        root_refspec_info,
    )


# for single branch we should clone shallow but for other we should clone deep
# this allows us to get merge-base to work and git can then merge by pulling the minimum
# amount of data
def create_clone_args(clone_info: CloneInfo) -> List[str]:
    """Creates git clone arguments based on the base origin."""
    args = [
        "clone",
        "--filter=tree:0",
    ]

    if clone_info.clone_flags & CloneFlags.SHALLOW:
        args += [
            "--depth",
            "1",
        ]
    if clone_info.clone_flags & CloneFlags.SPARSE:
        args += ["--sparse"]

    ref_spec_info = clone_info.root_refspec_info

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
        clone_info.url,
    ]

    return args


def normalize_merge_result(ret: int, out: str, err: str):
    if "CONFLICT" in out:
        return -1, out

    return ret, err


class RepoProcessor:
    """
    Processes a ProjectSpec by concurrently cloning and merging modules.
    """

    def __init__(
        self,
        workdir: Path,
        name: str,
        semaphore: asyncio.Semaphore,
        repo_info: RepoInfo,
        progress: Progress,
        count_progress: Progress,
        count_task: TaskID,
        concurrency: int,
    ):
        self.workdir = workdir
        self.name = name
        self.semaphore = semaphore
        self.repo_info = repo_info
        self.progress = progress
        self.count_progress = count_progress
        self.count_task = count_task
        self.concurrency = concurrency

    @deprecated(
        "run_shell_commands is deprecated if used to apply patches. Use patch_globs properties in spec.yaml instead."
    )
    async def run_shell_commands(self, repo_info: RepoInfo, module_path: Path) -> int:
        for cmd in repo_info.shell_commands:
            self.progress.update(self.task_id, status=f"Running shell command: {cmd}...")
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
                await run_git("am", "--abort", cwd=str(module_path))
                self.progress.update(
                    self.task_id,
                    status=f"[red]Shell command failed: {cmd}\nError: {stderr.decode().strip()}",
                )
                return -1
        return 0

    async def setup_new_repo(
        self,
        clone_info: CloneInfo,
        module_path: Path,
    ) -> int:
        root_refspec_info = clone_info.root_refspec_info
        remote = root_refspec_info.remote
        root_refspec = root_refspec_info.refspec

        self.progress.update(
            self.task_id,
            status=(f"Cloning {remote}/{root_refspec}"),
        )

        clone_args = create_clone_args(clone_info)
        ret, out, err = await run_git(*clone_args, module_path)

        if ret != 0:
            status_message = (
                f"[red]Clone failed {root_refspec_info.remote}({clone_info.url})/{root_refspec_info.refspec}"
                + f" -> {module_path}:\n{err}"
            )
            self.progress.update(self.task_id, status=status_message)
            return ret

        local_ref = get_local_ref(root_refspec_info)
        ret, out, err = await run_git("checkout", "-b", local_ref, cwd=module_path)

        return 0

    async def reset_repo_for_work(self, module_path: Path) -> int:
        # TODO(franz): we should test if the folder is a git repo or not

        ret, out, err = await run_git("status", "--porcelain", cwd=module_path)

        if out != "":
            self.progress.update(self.task_id, status=f"[red]Repo is dirty:\n{out}")
            return ret
        # Reset all the local origin to their remote origins
        repo_info = self.repo_info
        root_refspec_info = repo_info.refspec_info[0]

        self.progress.update(
            self.task_id,
            status=(f"Resetting existing repository for {root_refspec_info.remote}/{root_refspec_info.refspec}"),
        )

        s_ret, s_out, s_err = await run_git("rev-parse", "--is-shallow-repository", cwd=module_path)
        if len(repo_info.refspec_info) > 1 and s_out == "true":
            await run_git("fetch", "--unshallow", cwd=module_path)

        reset_target = get_local_ref(root_refspec_info)
        ret, out, err = await run_git("reset", "--hard", reset_target, cwd=module_path)
        if ret != 0:
            self.progress.update(self.task_id, status=f"[red]Reset failed: {err}")
            return ret

        return 0

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

                os.symlink(path_src_symlink.relative_to(links_path, walk_up=True), path_dest_symlink, True)
            except OSError as e:
                return -1, str(e)

        return 0, ""

    async def merge_spec_into_tree(
        self,
        spec: RepoInfo,
        refspec_info: RefspecInfo,
        root_refspec_info: RefspecInfo,
        module_path: Path,
    ) -> tuple[int, str]:
        # This is weird...
        remote_url = spec.remotes.get(refspec_info.remote) or refspec_info.remote

        local_ref = get_local_ref(refspec_info)
        remote_ref = refspec_info.refspec

        # Merge
        # I think the idea would be to not fetch shallow but fetch treeless and do a merge-base
        # then fetch the required data and then merge
        self.progress.update(self.task_id, status=f"Merging {local_ref}", advance=0.1)
        ret, out, err = await run_git("merge", "--no-edit", local_ref, cwd=module_path)
        ret, err = normalize_merge_result(ret, out, err)

        if "CONFLICT" in err:
            self.progress.update(self.task_id, status=f"[red]Merge conflict {local_ref} in {remote_ref}: {err}")
            # In case of conflict, we might want to abort the merge
            await run_git("merge", "--abort", cwd=module_path)
            return ret, err

        if ret != 0:
            self.progress.update(self.task_id, status=f"[red]Merge error {local_ref} in {remote_ref}: {err}")
            return ret, err

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

        ret, out, err = await run_git(*args, cwd=module_path)

        return ret, out, err

    def filter_non_link_module(self, spec: RepoInfo):
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

    async def setup_odoo_sparse(self, module_spec: RepoInfo, module_path: Path):
        list_modules = module_spec.modules

        await run_git("sparse-checkout", "init", "--no-cone", cwd=module_path)
        included_po = [f"{locale}.po" for locale in module_spec.locales]
        included_modules = [f"/addons/{module}/*" for module in list_modules]
        await run_git(
            "sparse-checkout",
            "set",
            "/*",
            "!/addons/*",
            *included_modules,
            "!*.po",
            *included_po,
            cwd=module_path,
        )

    async def setup_sparse_checkout(self, symlink_modules: List[str], module_path: Path):
        # 2. Sparse Checkout setup
        if self.name != "odoo":
            self.progress.update(self.task_id, status="Configuring sparse checkout...")
            await run_git("sparse-checkout", "init", "--cone", cwd=module_path)
            if symlink_modules:
                await run_git("sparse-checkout", "set", *self.repo_info.modules, cwd=module_path)
        elif len(self.repo_info.locales) > 0:
            # TODO(franz): We should still set sparse if there is no locales but there is a module list
            self.progress.update(self.task_id, status="Configuring sparse odoo checkout...")
            await self.setup_odoo_sparse(self.repo_info, module_path)

    async def process_repo(self) -> int:
        """Processes a single ModuleSpec."""
        symlink_modules = self.filter_non_link_module(self.repo_info)
        module_path = get_module_path(self.workdir, self.name, self.repo_info)

        async with self.semaphore:
            try:
                self.task_id = self.progress.add_task(
                    f"[cyan]{self.name}", status="Waiting...", total=len(self.repo_info.refspec_info) + 1
                )
                if not self.repo_info.refspec_info:
                    self.progress.update(self.task_id, status="[yellow]No origins defined", completed=1)
                    return -1

                # TODO(franz) the shallow and sparseness of repo should be unify
                # so that we don't have all those stupid conditions
                if check_path_is_repo(module_path):
                    clone_info = clone_info_from_repo(self.name, self.repo_info)
                    ret = await self.setup_new_repo(clone_info, module_path)
                else:
                    ret = await self.reset_repo_for_work(module_path)

                if ret != 0:
                    return -1

                await self.setup_sparse_checkout(symlink_modules, module_path)

                checkout_target = "merged"

                await run_git("checkout", "-b", checkout_target, cwd=module_path)
                self.progress.advance(self.task_id)

                for remote, remote_url in self.repo_info.remotes.items():
                    await run_git("remote", "add", remote, remote_url, cwd=module_path)
                    await run_git("config", f"remote.{remote}.partialCloneFilter", "tree:0", cwd=module_path)
                    await run_git("config", f"remote.{remote}.promisor", "true", cwd=module_path)

                refspec_by_remote: Dict[str, List[RefspecInfo]] = self.get_refspec_by_remote(
                    self.repo_info.refspec_info
                )

                for remote, refspec_list in refspec_by_remote.items():
                    self.progress.update(self.task_id, status=f"Fetching multi from {remote}")
                    await self.fetch_multi(remote, refspec_list, module_path)

                # 4. Fetch and Merge remaining origins
                for refspec_info in self.repo_info.refspec_info[1:]:
                    ret, err = await self.merge_spec_into_tree(
                        self.repo_info, refspec_info, self.repo_info.refspec_info[0], module_path
                    )
                    if ret != 0:
                        return -1
                    self.progress.advance(self.task_id)

                if self.repo_info.shell_commands:
                    ret = await self.run_shell_commands(self.repo_info, module_path)
                    if ret != 0:
                        return ret

                if self.repo_info.patch_globs_to_apply:
                    for glob in self.repo_info.patch_globs_to_apply:
                        self.progress.update(self.task_id, status=f"Applying patches: {glob}...", advance=0.1)
                        ret, out, err = await run_git("am", glob, cwd=module_path)
                        if ret != 0:
                            await run_git("am", "--abort", cwd=module_path)
                            self.progress.update(self.task_id, status=f"[red]Applying patches failed: {err}")
                            return ret

                self.progress.update(self.task_id, status="Linking directory")
                if self.name != "odoo":
                    ret, err = self.link_all_modules(symlink_modules, module_path)
                    if ret != 0:
                        self.progress.update(self.task_id, status=f"[red]Could not link modules: {err}")
                        return ret

                self.progress.update(self.task_id, status="[green]Complete", advance=1)
                self.progress.remove_task(self.task_id)
                self.count_progress.advance(self.count_task)

            except Exception as e:
                self.progress.update(self.task_id, status=f"[red]Error: {str(e)}")
                raise e
                return -1

        return 0


async def process_project(project_spec: ProjectSpec, concurrency: int) -> None:
    """Processes all modules in a ProjectSpec."""
    (project_spec.workdir / "external-src").mkdir(parents=True, exist_ok=True)

    task_list_progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.fields[status]}", table_column=Column(ratio=2)),
    )

    task_count_progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
    )
    count_task = task_count_progress.add_task("Processing Modules", total=len(project_spec.repos))

    progress_table = Table.grid()
    progress_table.add_row(
        task_list_progress,
    )
    progress_table.add_row(
        task_count_progress,
    )

    semaphore = asyncio.Semaphore(concurrency)
    with Live(progress_table, console=console, refresh_per_second=10):
        tasks = []
        for name, repo_info in project_spec.repos.items():
            total_steps = len(repo_info.refspec_info) + 1
            repo_processor = RepoProcessor(
                project_spec.workdir,
                name,
                semaphore,
                repo_info,
                task_list_progress,
                task_count_progress,
                count_task,
                concurrency,
            )
            tasks.append(repo_processor.process_repo())

        # this should error if a task crashes
        return_codes = await asyncio.gather(*tasks)
        if any(return_codes):
            raise Exception()
