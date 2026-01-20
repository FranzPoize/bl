# Async Spec Processor Plan

## Overview
The goal is to implement a high-performance, asynchronous processor for `ProjectSpec` that handles multiple `ModuleSpec` entries concurrently while providing real-time, multi-line status updates in the console.

## Research Basis
This plan incorporates strategies and tools identified in the following research:
- **CLI Library Research** ([dev_folder/cli_library_research.md](../cli_library_research.md)): Recommended using **Rich** for its native multi-line progress support and **Typer** for a clean CLI structure.
- **Spec Processor Research** ([dev_folder/spec_processor_research.md](../spec_processor_research.md)): Identified **shallow clones** (`--depth 1`), **blobless filters** (`--filter=blob:none`), and **sparse checkouts** as the most efficient way to process modules with minimal data transfer.

## Core Technologies
- **asyncio**: For concurrent execution of I/O-bound Git operations.
- **Rich**: For real-time, multi-line progress reporting (specifically the `Progress` and `Live` components).
- **Git**: Utilizing the optimized commands detailed in the research.

## Architecture

### 1. Concurrency Control
We will use an `asyncio.Semaphore` to limit the number of `ModuleSpec` processed simultaneously. This value will be configurable via the CLI (default: 4).

### 2. UI Strategy (Rich + Live)
Each `ModuleSpec` will have a dedicated line in the terminal.
- **Columns**: Task Description, Status Spinner, Progress Bar/Percentage, and a dynamic Error field.
- **Update Logic**: Updates will be pushed to the `Live` display from the async tasks.

### 3. Asynchronous Git Operations
Commands will be executed via `asyncio.create_subprocess_exec` to ensure the UI remains responsive. Key operations include:
- `git clone --no-checkout --filter=blob:none --depth 1`
- `git sparse-checkout init --cone`
- `git sparse-checkout set <modules>`
- `git fetch origin refs/pull/<id>/head` (for PRs)

### 4. Processing Workflow per Module
For each `ModuleSpec`:
1. **Initialize Task**: Add a line to the Rich progress display.
2. **Setup Workspace**: Create a directory in `modules/<name>`.
3. **Fetch & Merge**: 
   - Apply the logic from **Spec Processor Research** to fetch all `origins`.
   - Perform the merge sequence.
4. **Error Handling**: Capture any `CalledProcessError` or merge conflicts, display the error on the module's line, and move to the next task.

## Console Output Mockup
```
[✔] odoo            - Complete
[⠋] server-ux       - Fetching origin/17.0 (2/3)
[✖] queue           - Error: Merge conflict in queue_job/models/base.py
[⠙] web             - Cloning https://github.com/OCA/web...
```
