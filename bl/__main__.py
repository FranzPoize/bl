import argparse
import asyncio
import sys
from pathlib import Path

from bl.spec_parser import load_spec_file
from bl.spec_processor import process_project


def run():
    parser = argparse.ArgumentParser(
        description="Process a project specification.", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "-c", "--config", type=Path, help="Path to the project specification file.", default="spec.yaml"
    )
    parser.add_argument("-z", "--frozen", type=Path, help="Path to the frozen specification file.")
    parser.add_argument("-j", "--concurrency", type=int, default=28, help="Number of concurrent tasks.")
    parser.add_argument("-w", "--workdir", type=Path, help="Working directory. Defaults to config directory.")
    args = parser.parse_args()

    project_spec = load_spec_file(args.config, args.frozen, args.workdir)
    if project_spec is None:
        sys.exit(1)

    asyncio.run(process_project(project_spec, concurrency=args.concurrency))


if __name__ == "__main__":
    run()
