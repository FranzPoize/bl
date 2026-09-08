"""Real process concurrency and crash recovery without network access or sleeps."""

import asyncio
import sys
import textwrap

import pytest

from tests.odoo_worktree_helpers import OdooEnvironment, OdooProject

WORKER = textwrap.dedent("""\
    import asyncio
    import sys
    from pathlib import Path
    from bl import utils

    workdir = Path(sys.argv[1])
    pause_after_creation = sys.argv[2] == "pause"
    original_git = utils.run_git

    async def run_git(*args, **kwargs):
        result = await original_git(*args, **kwargs)
        creates_worktree = args[:2] == ("worktree", "add")
        creates_project_clone = args[0] == "clone" and str(workdir / "src") in [str(arg) for arg in args]
        if pause_after_creation and result[0] == 0 and (creates_worktree or creates_project_clone):
            print("PAUSED_AFTER_CREATION", flush=True)
            await asyncio.Event().wait()
        return result

    utils.run_git = run_git
    from bl.spec_parser import load_spec_file
    from bl.spec_processor import process_project

    print("READY", flush=True)
    assert sys.stdin.readline().strip() == "BUILD"
    project = load_spec_file(workdir / "spec.yaml", None, workdir, [])
    asyncio.run(process_project(project, concurrency=1))
    """)


async def _wait_for_line(process: asyncio.subprocess.Process, expected: bytes) -> None:
    assert process.stdout is not None
    async with asyncio.timeout(20):
        while True:
            line = await process.stdout.readline()
            if expected in line:
                return
            assert line, f"Build process exited before reaching {expected!r}"


async def _start(project: OdooProject, env: dict[str, str], *, pause: bool = False) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        WORKER,
        str(project.workdir),
        "pause" if pause else "build",
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    await asyncio.wait_for(process.communicate(), timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("already_built", [False, True], ids=["first-build", "branch-update"])
async def test_concurrent_processes_publish_one_shared_worktree(
    odoo_store: OdooEnvironment, already_built: bool
) -> None:
    a, b = odoo_store.project("a"), odoo_store.project("b")
    if already_built:
        await a.build()
        await b.build()
        odoo_store.advance()
    latest = odoo_store.git(odoo_store.remote, "rev-parse", "18.0")
    processes = []
    try:
        for project in (a, b):
            processes.append(await _start(project, odoo_store.env))
        # Both interpreters have imported BL before either enters the build.
        # A pipe barrier avoids assumptions about process startup speed.
        await asyncio.gather(*(_wait_for_line(process, b"READY") for process in processes))
        for process in processes:
            assert process.stdin is not None
            process.stdin.write(b"BUILD\n")
            await process.stdin.drain()
        outputs = await asyncio.wait_for(asyncio.gather(*(p.communicate() for p in processes)), timeout=20)
        for process, (output, _) in zip(processes, outputs):
            assert process.returncode == 0, output.decode()
    finally:
        for process in processes:
            if process.returncode is None:
                await _stop(process)

    for project in (a, b):
        assert odoo_store.git(project.source, "rev-parse", "HEAD") == latest
        assert odoo_store.git(project.source, "status", "--porcelain") == ""
    assert a.source.resolve() == b.source.resolve()
    registrations = odoo_store.git(a.source, "worktree", "list", "--porcelain")
    assert registrations.count(f"worktree {a.source.resolve()}\n") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("patched", [False, True], ids=["basic-branch", "patched-branch"])
async def test_killed_preparation_can_be_retried_without_publishing_incomplete_source(
    odoo_store: OdooEnvironment,
    patched: bool,
) -> None:
    patches = odoo_store.patch_series("required fix") if patched else []
    a, b = odoo_store.project("a", patches), odoo_store.project("b", patches)
    process = await _start(a, odoo_store.env, pause=True)
    try:
        await _wait_for_line(process, b"READY")
        assert process.stdin is not None
        process.stdin.write(b"BUILD\n")
        await process.stdin.drain()
        await _wait_for_line(process, b"PAUSED_AFTER_CREATION")
        # Capture this before retry: publishing an unpatched checkout even
        # temporarily violates the source contract for a first build.
        published_incomplete_source = a.source.exists()
    finally:
        await _stop(process)

    await asyncio.wait_for(a.build(), timeout=20)
    await asyncio.wait_for(b.build(), timeout=20)
    for project in (a, b):
        assert (project.source / "odoo/message.txt").read_text() == ("required fix\n" if patched else "original\n")
        assert odoo_store.git(project.source, "status", "--porcelain") == ""
    assert not published_incomplete_source, "An incomplete checkout must stay private until publication"
    assert a.source.resolve() == b.source.resolve()
    assert not list(odoo_store.store_root.glob("worktrees/*/.preparing-*"))
