import sys
from bl.spec_parser import load_spec_file
from bl.spec_processor import process_project
from pathlib import Path
import asyncio

if __name__ == "__main__":
    # Example usage:
    file_name = sys.argv[1] if len(sys.argv) > 1 else "spec.yaml"
    spec_file = file_name
    project_spec = load_spec_file(spec_file)
    asyncio.run(process_project(project_spec, workdir=Path("./test_dir"), concurrency=16))
