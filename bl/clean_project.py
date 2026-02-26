import shutil
from pathlib import Path

from bl.spec_processor import console
from bl.types import ProjectSpec


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


def clean_project(project_spec: ProjectSpec, non_interactive: bool = False) -> int:
    """Clean src and external-src directories under the project workdir."""
    workdir = project_spec.workdir
    failed = False

    targets = [workdir / "src", workdir / "external-src"]

    for target in targets:
        deletion_failed = _clean_directory(target, non_interactive)
        if deletion_failed:
            failed = True

    return 1 if failed else 0
