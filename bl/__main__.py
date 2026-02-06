import argparse
import asyncio
import sys
from pathlib import Path

from bl.spec_parser import load_spec_file
from bl.spec_processor import process_project
from bl.freezer import freeze_project


def run():
    parser = argparse.ArgumentParser(
        description="Process a project specification.", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "-c", "--config", type=Path, help="Path to the project specification file.", default="spec.yaml"
    )
    parent_parser.add_argument("-z", "--frozen", type=Path, help="Path to the frozen specification file.")
    parent_parser.add_argument("-j", "--concurrency", type=int, default=28, help="Number of concurrent tasks.")
    parent_parser.add_argument("-w", "--workdir", type=Path, help="Working directory. Defaults to config directory.")

    sub = parser.add_subparsers(help="subcommand help", dest="command")
    build = sub.add_parser("build", parents=[parent_parser], help="build help")
    freeze = sub.add_parser("freeze", parents=[parent_parser], help="freeze help")

    args = parser.parse_args()

    project_spec = load_spec_file(args.config, args.frozen, args.workdir)
    if project_spec is None:
        sys.exit(1)

    try:
        if args.command == "freeze":
            asyncio.run(freeze_project(project_spec, args.freeze, concurrency=args.concurrency))
        elif args.command == "build":
            asyncio.run(process_project(project_spec, concurrency=args.concurrency))
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    run()
