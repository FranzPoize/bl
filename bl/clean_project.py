import asyncio
import shutil
from pathlib import Path

from bl.spec_processor import console
from bl.types import ProjectSpec, RepoInfo
from bl.utils import format_diff, get_module_path, run_git


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


async def gather_dirty_repo_info(project_spec: ProjectSpec) -> list[RepoInfo]:
    workdir = project_spec.workdir
    dirty_repo_infos: list[RepoInfo] = []

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


async def reset_dirty_repos(project_spec: ProjectSpec, dirty_repo_infos: list[RepoInfo]) -> bool:
    dirty_found = len(dirty_repo_infos)
    cleaned = 0
    skipped = 0
    failed = 0

    for name, repo_info, output, module_path in dirty_repo_infos:
        console.print(f"[yellow]Repo is dirty:[/] [cyan]{name}[/] at {module_path}")
        console.print(format_diff(output))

        answer = input(f"Clean repo {name} at {module_path} with 'git reset --hard'? [y/N]: ").strip().lower()
        if answer != "y":
            console.print(f"[cyan]Skipped dirty repo:[/] {name} at {module_path}")
            skipped += 1
            continue

        while True:
            ret, out, err = await reset_repo(module_path)

            if ret != 1:
                break

            git_index = module_path / ".git" / "index.lock"
            if not git_index.exists():
                failed += 1
                console.print("[red]Failed to cleanup index.lock")
                break

            answer = input(f"Remove index lock at {git_index} ? [y/N]").strip().lower()
            if answer != "y":
                break
            git_index.unlink()

        console.print(f"[cyan]Cleaned repo with 'git reset --hard':[/] {name} at {module_path}")
        cleaned += 1

    if dirty_found:
        console.print(
            f"[bold]Dirty repos:[/] found={dirty_found}, cleaned={cleaned}, skipped={skipped}, failed={failed}"
        )

    return failed


async def _clean_dirty_repos(project_spec: ProjectSpec) -> bool:
    """Interactively clean dirty git repositories with `git reset --hard`.

    Returns True if any repo cleaning failed, False otherwise.
    """
    dirty_repo_infos = await gather_dirty_repo_info(project_spec)

    return await reset_dirty_repos(project_spec, dirty_repo_infos)


def clean_project(
    project_spec: ProjectSpec,
    non_interactive: bool = False,
    clean_dirty_repos: bool = False,
) -> int:
    """Clean src and external-src directories under the project workdir.

    Optionally, also scan all repos for dirty state and offer to reset them.
    """
    workdir = project_spec.workdir
    failed = False

    targets = [workdir / "src", workdir / "external-src"]

    if clean_dirty_repos:
        dirty_failed = asyncio.run(_clean_dirty_repos(project_spec))
        if dirty_failed:
            failed = True
    else:
        for target in targets:
            deletion_failed = _clean_directory(target, non_interactive)
            if deletion_failed:
                failed = True

    return 1 if failed else 0
