import shutil
from pathlib import Path

from bl.spec_processor import console
from bl.types import ProjectSpec, RepoInfo
from bl.utils import format_diff, get_module_path, run_git, unlink_path


# TODO(franz) It should list all the target_folders in the spec and delete all of those
def _clean_directory(path: Path, non_interactive: bool) -> bool:
    """Return True if a deletion failed, False otherwise."""
    abs_path = path.resolve()

    if not path.exists() or not path.is_dir():
        console.print(f"[yellow]Directory does not exist:[/] {abs_path}")
        return False

    if non_interactive:
        try:
            shutil.rmtree(abs_path)
            console.print(f"[cyan]Deleted:[/] {abs_path}")
        except OSError as e:
            console.print(f"[red]Failed to delete {abs_path}:[/] {e}")
            return True
        return False

    answer = input(f"Delete {abs_path}? [y/N]: ").strip().lower()
    if answer != "y":
        console.print(f"[cyan]Skipped:[/] {abs_path}")
        return False

    try:
        shutil.rmtree(abs_path)
        console.print(f"[cyan]Deleted:[/] {abs_path}")
    except OSError as e:
        console.print(f"[red]Failed to delete {abs_path}:[/] {e}")
        return True

    return False


async def reset_repo(module_path: Path):
    ret, out, err = await run_git(
        "reset",
        "--hard",
        "HEAD",
        cwd=module_path,
    )
    if ret != 0:
        if "index.lock" in err:
            return 1, out, err
        return -1, out, err

    return ret, out, err


async def gather_dirty_repo_info(project_spec: ProjectSpec) -> list[tuple[str, RepoInfo, str, Path]]:
    workdir = project_spec.workdir
    dirty_repo_infos: list[tuple[str, RepoInfo, str, Path]] = []

    for name, repo_info in project_spec.repos.items():
        module_path = get_module_path(workdir, name, repo_info)
        git_dir = module_path / ".git"

        if not git_dir.exists():
            console.print(f"[yellow]Skipping:[/] {module_path} (no .git directory)")
            continue

        ret, out, err = await run_git("status", "--porcelain", cwd=module_path)

        if out != "":
            dirty_repo_infos.append((name, repo_info, out, module_path))

    return dirty_repo_infos


async def show_diffs(project_spec: ProjectSpec):
    dirty_repo_infos = await gather_dirty_repo_info(project_spec)
    for name, _, _, module_path in dirty_repo_infos:
        ret, out, err = await run_git("diff", cwd=module_path)
        if out:
            console.print(f"[bold cyan]Diff for {name} at {module_path}:[/]")
            console.print(out)
        ret, out, err = await run_git("diff", "--cached", cwd=module_path)
        if out:
            console.print(f"[bold cyan]Staged diff for {name} at {module_path}:[/]")
            console.print(out)


async def handle_dirty_repos(project_spec: ProjectSpec, dry_run: bool) -> int:
    """Check for dirty repositories and offer to reset them.

    Returns 1 if failed, 0 otherwise.
    """
    dirty_repo_infos = await gather_dirty_repo_info(project_spec)
    for name, repo_info, output, module_path in dirty_repo_infos:
        console.print(f"[yellow]Repo is dirty:[/] [cyan]{name}[/] at {module_path}")
        console.print(format_diff(output))

    if not dirty_repo_infos:
        console.print("[cyan]No dirty repositories.[/]")
        return 0

    if dry_run:
        console.print("[cyan]Would reset dirty repositories.[/]")
        return 0

    console.print("[bold red]Warning: there are dirty repositories![/]")
    answer = input("Reset dirty repositories? [y/N]: ").strip().lower()
    if answer != "y":
        console.print("[cyan]Aborted.[/]")
        return 1

    failed = False
    for name, repo_info, output, module_path in dirty_repo_infos:
        console.print(f"[yellow]Reset:[/] {name}")
        ret, out, err = await reset_repo(module_path)
        if ret != 0:
            console.print(f"[red]Failed to reset {name}:[/] {err}")
            failed = True

    return 1 if failed else 0


async def handle_unlink(workdir: Path, dry_run: bool) -> int:
    """Remove links folder contents.

    Returns 1 if failed, 0 otherwise.
    """
    links_path = workdir / "links"
    if not links_path.exists():
        return 0

    children = sorted(links_path.iterdir())
    if dry_run:
        for child in children:
            console.print(f"[cyan]Would unlink:[/] {child}")
        return 0

    failed = False
    for child in children:
        ret, err = await unlink_path(child)
        if ret != 0:
            console.print(f"[red]Failed to unlink {child}:[/] {err}")
            failed = True

    return 1 if failed else 0


async def handle_remove(workdir: Path, force: bool, dry_run: bool) -> int:
    """Delete src and external-src directories.

    Returns 1 if failed, 0 otherwise.
    """
    targets = [workdir / "src", workdir / "external-src"]
    failed = False

    for target in targets:
        if dry_run:
            if target.exists():
                console.print(f"[cyan]Would delete:[/] {target}")
            continue

        deletion_failed = _clean_directory(target, force)
        if deletion_failed:
            failed = True

    return 1 if failed else 0


async def clean_project(
    project_spec: ProjectSpec,
    remove: bool = False,
    unlink: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    """Clean src and external-src directories under the project workdir.

    Behavior depends on flags:
    - No flags: Check dirty repositories and offer to reset them
    - remove: Delete src and external-src directories
    - unlink: Remove links folder contents
    - dry_run: Show what would be done without executing
    """
    workdir = project_spec.workdir
    failed = False

    if not remove:
        if await handle_dirty_repos(project_spec, dry_run):
            failed = True

    if unlink:
        if await handle_unlink(workdir, dry_run):
            failed = True

    if remove:
        if await handle_remove(workdir, force, dry_run):
            failed = True

    return 1 if failed else 0
