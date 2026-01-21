import sys
from bl.spec_parser import load_spec_file
from bl.spec_processor import process_project
from pathlib import Path
import asyncio


def run():
    # Example usage:
    file_name = sys.argv[1] if len(sys.argv) > 1 else "spec.yaml"
    spec_file = file_name
    project_spec = load_spec_file(spec_file)
    if project_spec is not None:
        asyncio.run(process_project(project_spec, workdir=Path("."), concurrency=16))


if __name__ == "__main__":
    run()
