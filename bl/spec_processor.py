import asyncio
import logging
import os
import warnings
from pathlib import Path
from typing import Dict, List

from rich.console import Console
from rich.live import Live
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TaskID, TextColumn
from rich.table import Column, Table

from bl.types import CloneFlags, CloneInfo, OriginType, ProjectSpec, RefspecInfo, RepoInfo
from bl.utils import english_env, format_diff, get_local_ref, get_module_path, run_git

console = Console()
logger = logging.getLogger(__name__)

# TODO(franz): it's a bit better now but better keep an eye on it
# TODO(franz): Error handling should be watch carefully because if
# we don't exit on some error code due to the fact that git resolve to
# the parent repo we could activate sparse checkout on a parent folder
# should probably make a function that handles the error in a unified manner
# and crash if the error is on a vital part of the process


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
        "--config",
        "feature.manyFiles=true",
        "--config",
        "feature.experimental=true",
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
        use_bindfs: bool = False,
    ):
        self.workdir = workdir
        self.name = name
        self.semaphore = semaphore
        self.repo_info = repo_info
        self.progress = progress
        self.count_progress = count_progress
        self.count_task = count_task
        self.concurrency = concurrency
        self.use_bindfs = use_bindfs

    async def clone_or_reset_and_setup_repo(self, module_path: Path, symlink_modules: List[str]):
        clone_info = clone_info_from_repo(self.name, self.repo_info)
        if check_path_is_repo(module_path):
            ret = await self.setup_new_repo(clone_info, module_path)
        else:
            ret = await self.reset_repo_for_work(module_path)

        if ret != 0:
            return -1

        ret, _, _ = await self.check_main_remote(module_path)

        for remote, remote_url in self.repo_info.remotes.items():
            await run_git("remote", "add", remote, remote_url, cwd=module_path)
            await run_git("config", f"remote.{remote}.partialCloneFilter", "tree:0", cwd=module_path)
            await run_git("config", f"remote.{remote}.promisor", "true", cwd=module_path)

        return 0

    async def run_shell_commands(self, repo_info: RepoInfo, module_path: Path) -> int:
        for cmd in repo_info.shell_commands:
            if "git am" in cmd:
                warnings.warn(
                    "run_shell_commands is deprecated if used to apply patches. "
                    + "Use patch_globs properties in spec.yaml instead.",
                    DeprecationWarning,
                )
                cmd_args = cmd.split(" ")
                glob = cmd_args[-1]
                ret, err = await self.check_and_apply_patch(glob, module_path)
                self.progress.update(
                    self.task_id,
                    status=f"[red]Applying patch {glob}: {err}",
                )
                return ret

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
        ret, out, err = await run_git("status", "--porcelain", cwd=module_path)

        if out != "":
            self.progress.update(self.task_id, status=f"[red]Repo is dirty:\n{format_diff(out)}")
            return -1
        if ret != 0:
            self.progress.update(self.task_id, status="[red]Repo does not exist")
            return ret
        # Reset all the local origin to their remote origins
        repo_info = self.repo_info
        root_refspec_info = repo_info.refspec_info[0]

        self.progress.update(
            self.task_id,
            status=f"Resetting existing repository for {root_refspec_info.remote}/{root_refspec_info.refspec}",
        )
        return 0

    async def check_main_remote(self, module_path: Path) -> tuple[int, str, str]:
        if len(self.repo_info.remotes) != 1:
            return 0, "", ""

        remote_name, remote_url = next(iter(self.repo_info.remotes.items()))

        ret, out, err = await run_git("remote", "get-url", remote_name, cwd=module_path)
        if ret != 0:
            return ret, out, err

        current_url = out.strip()
        if current_url != remote_url:
            await run_git("remote", "remove", remote_name, cwd=module_path)
            ret, out, err = await run_git("remote", "add", remote_name, remote_url, cwd=module_path)
            if ret != 0:
                return ret, out, err
            await run_git("config", f"remote.{remote_name}.partialCloneFilter", "tree:0", cwd=module_path)
            await run_git("config", f"remote.{remote_name}.promisor", "true", cwd=module_path)

        return 0, "", ""

    async def unshallow_if_necessary(self, module_path: Path):
        s_ret, s_out, s_err = await run_git("rev-parse", "--is-shallow-repository", cwd=module_path)
        if len(self.repo_info.refspec_info) > 1 and s_out == "true":
            ret, out, err = await run_git("fetch", "--unshallow", cwd=module_path)

            if ret != 0:
                self.progress.update(self.task_id, status=f"Unshallow unsucessful for {self.name}")
            return ret, out, err
        return 0, "", ""

    async def checkout_or_create_base_branch(
        self, base_refspec: RefspecInfo, module_path: Path
    ) -> tuple[int, str, str]:
        refspec = base_refspec.ref_name if base_refspec.ref_name else base_refspec.refspec

        ret, _, _ = await run_git("rev-parse", "--verify", refspec, cwd=module_path)
        has_base_branch = ret == 0

        if has_base_branch:
            ret, out, err = await run_git(
                "checkout",
                refspec,
                cwd=module_path,
            )
            return ret, out, err

        ret, _, _ = await run_git("rev-parse", "--verify", f"origin/{refspec}", cwd=module_path)
        has_remote_branch = ret == 0
        if has_remote_branch:
            ret, out, err = await run_git(
                "checkout",
                "--track",
                f"origin/{refspec}",
                cwd=module_path,
            )
            return ret, out, err

        return (
            -1,
            "",
            f"Can't find base branch {refspec}",
        )

    async def setup_main_branch(self, module_path: Path) -> int:
        ret, out, err = await run_git("rev-parse", "--verify", "merged", cwd=module_path)
        should_have_merged_branch = len(self.repo_info.refspec_info) > 1
        has_merged_branch = ret == 0

        base_refspec = self.repo_info.refspec_info[0]
        ret, out, err = await self.checkout_or_create_base_branch(base_refspec, module_path)
        if ret != 0:
            self.progress.update(
                self.task_id,
                status=f"[ref]Could not checkout base branch {base_refspec.ref_name or base_refspec.refspec}: {err}",
            )
            return ret, out, err

        if has_merged_branch and not should_have_merged_branch:
            await run_git("branch", "-D", "merged", cwd=module_path)

        if should_have_merged_branch:
            ret, out, err = await run_git("switch", "-C", "merged", cwd=module_path)

        return 0, "", ""

    async def link_all_modules(self, module_list: List[str], module_path: Path) -> tuple[int, str]:
        links_path = self.workdir / "links"
        await asyncio.to_thread(links_path.mkdir, exist_ok=True)

        for module_name in module_list:
            try:
                src_path = module_path / module_name
                dest_path = links_path / module_name

                if dest_path.is_symlink():
                    await asyncio.to_thread(os.unlink, dest_path)
                elif dest_path.is_mount():
                    umount = await asyncio.create_subprocess_exec("umount", str(dest_path), env=english_env)
                    await umount.wait()
                if dest_path.is_dir():
                    await asyncio.to_thread(dest_path.rmdir)

                if self.use_bindfs:
                    # Create the destination directory if it doesn't exist, bindfs requires it to exist
                    await asyncio.to_thread(dest_path.mkdir, exist_ok=True)
                    mount = await asyncio.create_subprocess_exec(
                        "bindfs",
                        "-n",
                        str(src_path),
                        str(dest_path),
                        env=english_env,
                    )
                    if await mount.wait() != 0:
                        return -1, f"Failed to bind mount {src_path} to {dest_path}"

                else:
                    await asyncio.to_thread(os.symlink, src_path.relative_to(links_path, walk_up=True), dest_path, True)
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
        local_ref = get_local_ref(refspec_info)
        remote_ref = refspec_info.refspec

        self.progress.update(self.task_id, status=f"Merging {local_ref}")
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
            "-a",
            "--update-head-ok",
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
            if path.is_symlink() or path.is_mount() or not path.exists():
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
            if symlink_modules:
                await run_git("sparse-checkout", "set", *self.repo_info.modules, cwd=module_path)
        elif len(self.repo_info.locales) > 0:
            # TODO(franz): We should still set sparse if there is no locales but there is a module list
            self.progress.update(self.task_id, status="Configuring sparse odoo checkout...")
            await self.setup_odoo_sparse(self.repo_info, module_path)

    def count_step(self):
        # steps are:
        # setup or clone
        # + fetches (number of remotes)
        # + merges (refspec_info - 1)
        # + potential Shell commands
        # + patch to apply
        # + links
        clone_steps = 1
        fetch_steps = len(self.repo_info.remotes)
        merge_count = len(self.repo_info.refspec_info) - 1
        command_count = len(self.repo_info.shell_commands)
        patch_count = len(self.repo_info.patch_globs_to_apply)
        link_step = 1
        count_step = clone_steps + fetch_steps + merge_count + command_count + patch_count + link_step

        return count_step

    async def check_and_apply_patch(self, glob: str, module_path: Path) -> tuple[int, str]:
        patch_files = [p.relative_to(module_path) for p in list(module_path.glob(glob))]
        c_ret, c_out, c_err = await run_git("apply", "--reverse", "--check", *patch_files, cwd=module_path)
        if c_ret == 0:
            # Patch is already applied we don't need to do it again
            return 0, ""
        ret, out, err = await run_git("am", *patch_files, cwd=module_path)
        if ret != 0:
            await run_git("am", "--abort", cwd=module_path)
            return -1, err
        return 0, ""

    async def process_repo(self) -> int:
        """Processes a single ModuleSpec."""
        symlink_modules = self.filter_non_link_module(self.repo_info)
        module_path = get_module_path(self.workdir, self.name, self.repo_info)

        async with self.semaphore:
            # As an input we have 3 things for each repo
            # a list of branches
            # a list of remotes
            # a list of modules
            try:
                count_step = self.count_step()
                self.task_id = self.progress.add_task(
                    f"[cyan]{self.name}",
                    status="Waiting...",
                    total=count_step,
                )
                if not self.repo_info.refspec_info:
                    self.progress.update(self.task_id, status="[yellow]No origins defined", completed=1)
                    return -1

                # First thing we need to do is setup the repos
                # - If the repo does not exist we need to clone it
                # - then we add all the remote
                #   - We should check if the remote are properly created
                #     remote can already created I don't thinkk git notifies
                #     us if the remote is not reachable

                self.progress.update(
                    self.task_id,
                    status=("Setting up repo ..."),
                )
                ret = await self.clone_or_reset_and_setup_repo(module_path, symlink_modules)
                self.progress.advance(self.task_id)

                if ret != 0:
                    return ret

                refspec_by_remote: Dict[str, List[RefspecInfo]] = self.get_refspec_by_remote(
                    self.repo_info.refspec_info
                )

                ret, _, _ = await self.unshallow_if_necessary(module_path)

                if ret != 0:
                    return ret

                # TODO(franz): right now we fetch everything so when the repo is just cloned
                # we fetch the base branch twice. Since we fetch with multi this is probably not
                # a big issue but it could be better
                # INFO(franz): I think this ok right now it's keep the code simple and the data
                # flow is better this way and the performance gain is I think small
                # One idea would be to use bare repo so that we fetch absolutely nothing on clone
                for remote, refspec_list in refspec_by_remote.items():
                    self.progress.update(self.task_id, status=f"Fetching multi from {remote}")
                    f_ret, f_out, f_err = await self.fetch_multi(remote, refspec_list, module_path)
                    if f_ret != 0:
                        logger.debug(f"Error fetching {self.name} - {remote}: {f_err}")
                    self.progress.advance(self.task_id)

                ret, _, _ = await self.setup_main_branch(module_path)
                if ret != 0:
                    return ret

                # Merge everything into the main branch
                for refspec_info in self.repo_info.refspec_info[1:]:
                    ret, err = await self.merge_spec_into_tree(
                        self.repo_info, refspec_info, self.repo_info.refspec_info[0], module_path
                    )
                    if ret != 0:
                        return -1
                    self.progress.advance(self.task_id)

                # We sparse checkout after the merge because it's faster to do it
                # in this order
                await self.setup_sparse_checkout(symlink_modules, module_path)

                ret = await self.run_shell_commands(self.repo_info, module_path)
                if ret != 0:
                    return ret
                self.progress.advance(self.task_id)

                for glob in self.repo_info.patch_globs_to_apply:
                    self.progress.update(self.task_id, status=f"Applying patches: {glob}...")
                    ret, err = await self.check_and_apply_patch(glob, module_path)
                    if ret != 0:
                        self.progress.update(self.task_id, status=f"[red]Applying patches failed: {err}")
                        return ret
                    self.progress.advance(self.task_id)

                self.progress.update(self.task_id, status="Linking directory")
                if self.name != "odoo":
                    ret, err = await self.link_all_modules(symlink_modules, module_path)
                    if ret != 0:
                        self.progress.update(self.task_id, status=f"[red]Could not link modules: {err}")
                        return ret

                self.count_progress.advance(self.count_task)
                self.progress.remove_task(self.task_id)

            except Exception as e:
                self.progress.update(self.task_id, status=f"[red]Error: {str(e)}")
                raise e
                return -1

        return 0


async def process_project(project_spec: ProjectSpec, concurrency: int, use_bindfs: bool = False) -> None:
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
            repo_processor = RepoProcessor(
                project_spec.workdir,
                name,
                semaphore,
                repo_info,
                task_list_progress,
                task_count_progress,
                count_task,
                concurrency,
                use_bindfs,
            )
            tasks.append(repo_processor.process_repo())

        # this should error if a task crashes
        return_codes = await asyncio.gather(*tasks)
        if any(return_codes):
            raise Exception()
