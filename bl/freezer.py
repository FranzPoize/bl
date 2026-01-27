import asyncio
import yaml
from operator import countOf
from pathlib import Path
from typing import TextIO

from rich.console import Console
from rich.live import Live
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TaskID, TextColumn
from bl.spec_parser import ModuleSpec, ProjectSpec
from bl.utils import get_local_ref, get_module_path, run_git

console = Console()


async def freeze_spec(
    sem: asyncio.Semaphore,
    progress: Progress,
    task_id: TaskID,
    module_name: str,
    module_spec: ModuleSpec,
    workdir: Path,
):
    result = {module_name: {}}
    async with sem:
        module_path = get_module_path(workdir, module_name, module_spec)

        for refspec_info in module_spec.refspec_info:
            local_ref = get_local_ref(refspec_info)
            ret, out, err = await run_git("rev-list", "--max-count", "1", local_ref, cwd=module_path)

            ref_name = refspec_info.ref_name or refspec_info.refspec

            data = result[module_name].get(refspec_info.remote, {})
            data[ref_name] = out
            result[module_name][refspec_info.remote] = data
        progress.advance(task_id)

    return result


async def freeze_project(project_spec: ProjectSpec, freeze_file: Path | bool, concurrency: int):
    frz_semaphore = asyncio.Semaphore(concurrency)
    workdir = project_spec.workdir
    freeze_file_name = freeze_file if freeze_file is not True else "frozen.yaml"
    freeze_file_path = workdir / freeze_file_name

    task_count_progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
    )
    count_task = task_count_progress.add_task(
        f"Freezing modules into {freeze_file_path}", total=len(project_spec.specs)
    )

    freeze_data = {}

    with Live(task_count_progress, console=console, refresh_per_second=10):
        task_list = []
        for name, spec in project_spec.specs.items():
            task_list.append(
                freeze_spec(
                    frz_semaphore,
                    task_count_progress,
                    count_task,
                    name,
                    spec,
                    workdir,
                )
            )
        freeze_list = await asyncio.gather(*task_list)
        for item in freeze_list:
            freeze_data.update(item)

    console.print(yaml.dump(freeze_data, default_flow_style=False))
    with open(freeze_file_path, "w") as freeze_stream:
        yaml.dump(freeze_data, freeze_stream, default_flow_style=False)

    return 0
