from pathlib import Path

from rich.console import Console

from bl.config import get_config_file, load_config, write_config
from bl.spec_parser import load_spec_file
from bl.utils import get_module_path, remove_locking_pre_commit, run, run_git

console = Console()


async def make_editable(repository_name: str, spec: Path, workdir: Path):
    project_spec = load_spec_file(spec, None, workdir, [])
    if str(repository_name) not in project_spec.repos:
        console.log(f"[red][yellow]{repository_name}[/] not in spec")
    module_path = get_module_path(
        project_spec.workdir,
        repository_name,
        project_spec.repos[str(repository_name)],
    )

    if not module_path.exists():
        console.err(f"[red]Repository {repository_name} does not exists[/]")

    # git fetch --unshallow origin 2>/dev/null || git fetch origin
    ret, out, err = await run_git("fetch", "--unshallow", cwd=module_path)

    if ret != 0:
        ret, out, err = await run_git("fetch", "origin", cwd=module_path)

        if ret != 0:
            console.print(f"[red]Editable error:[/] Couldn't fetch repo\n{err}")
    console.print(f"[green]Repo [yellow]{repository_name}[/] unshallowed[/]")

    # git sparse-checkout disable
    ret, out, err = await run_git("sparse-checkout", "disable", cwd=module_path)
    console.print(f"[green]Repo [yellow]{repository_name}[/] unsparsed[/]")
    # git config --unset extensions.partialClone 2>/dev/null || true
    ret, out, err = await run_git("config", "--unset", "extensions.partialClone", cwd=module_path)
    # git config --unset remote.origin.promisor 2>/dev/null || true
    ret, out, err = await run_git("config", "--unset", "promisor", cwd=module_path)
    # git config --unset remote.origin.partialclonefilter 2>/dev/null || true
    ret, out, err = await run_git("config", "--unset", "remote.origin.partialclonefilter", cwd=module_path)
    console.print(f"[green]Repo [yellow]{repository_name}[/] reconfigured[/]")
    # git fetch --refetch origin
    ret, out, err = await run_git("fetch", "--refetch", "origin", cwd=module_path)
    console.print(f"[green]Repo [yellow]{repository_name}[/] fetched fully[/]")
    ret, out, err = await remove_locking_pre_commit(module_path)
    console.print(f"[green]Removed commit locking pre-commit in [yellow]{repository_name}[/][/]")

    project_name = project_spec.workdir.absolute().parent.stem
    project_config_file_path = get_config_file(project_name)
    project_config_file = load_config(project_name)

    if "editable" not in project_config_file:
        project_config_file["editable"] = {}

    project_config_file["editable"][str(repository_name)] = "True"

    write_config(project_name, project_config_file)

    console.print(f"[green][yellow]{repository_name}[/] dev status saved[/] in {project_config_file_path}")
