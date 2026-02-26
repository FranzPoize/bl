import argparse
import asyncio
import logging
import sys
from pathlib import Path

from bl.clean_project import clean_project
from bl.freezer import freeze_project
from bl.spec_parser import load_spec_file
from bl.spec_processor import console, process_project


class RichConsoleHandler(logging.Handler):
    def __init__(self, rich_console, level=logging.NOTSET):
        super().__init__(level)
        self._console = rich_console

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            level = record.levelno
            if level >= logging.CRITICAL:
                level_style = "bold red"
            elif level >= logging.ERROR:
                level_style = "red"
            elif level >= logging.WARNING:
                level_style = "yellow"
            elif level >= logging.INFO:
                level_style = "cyan"
            else:
                level_style = "dim"

            self._console.print(f"[{level_style}]{record.levelname}[/]: {msg}")
            if record.exc_info:
                self._console.print_exception()
        except Exception:
            self.handleError(record)


def setup_logging(log_level: str) -> None:
    level = getattr(logging, log_level, logging.WARNING)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    handler = RichConsoleHandler(console)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(handler)


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
    parent_parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="WARNING",
        help="Logging level.",
    )

    sub = parser.add_subparsers(help="subcommand help", dest="command")
    sub.add_parser("build", parents=[parent_parser], help="build help")
    sub.add_parser("freeze", parents=[parent_parser], help="freeze help")
    clean_parser = sub.add_parser("clean", parents=[parent_parser], help="Clean src and external-src in workdir")
    clean_parser.add_argument(
        "--i-am-stupid",
        action="store_true",
        help="Delete src and external-src without prompting for confirmation.",
    )

    args = parser.parse_args()

    level_name = args.log_level
    setup_logging(level_name)

    project_spec = load_spec_file(args.config, args.frozen, args.workdir)
    if project_spec is None:
        sys.exit(1)

    try:
        if args.command == "freeze":
            asyncio.run(freeze_project(project_spec, args.frozen, concurrency=args.concurrency))
        elif args.command == "build":
            asyncio.run(process_project(project_spec, concurrency=args.concurrency))
        elif args.command == "clean":
            ret = clean_project(project_spec, non_interactive=args.i_am_stupid)
            if ret != 0:
                sys.exit(1)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    run()
