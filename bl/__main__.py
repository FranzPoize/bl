import argparse
import asyncio
import atexit
import logging
import logging.handlers
import queue
import sys
from pathlib import Path

from rich.console import Console

from bl.clean_project import clean_project
from bl.freezer import freeze_project
from bl.spec_parser import load_spec_file
from bl.spec_processor import process_project

err_console = Console(stderr=True)
out_console = Console()


class RichConsoleHandler(logging.Handler):
    def __init__(self, level=logging.NOTSET):
        super().__init__(level)
        self._console = out_console
        self._err_console = err_console

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

            message = f"[{level_style}]{record.levelname}[/]: {msg}"
            if level >= logging.ERROR or level >= logging.DEBUG:
                self._err_console.print(message)
            else:
                self._console.print(message)
            if record.exc_info:
                self._console.print_exception()
        except Exception:
            self.handleError(record)


que = queue.Queue(-1)
queue_handler = logging.handlers.QueueHandler(que)
listener = logging.handlers.QueueListener(que, RichConsoleHandler())


def setup_logging(log_level: str) -> None:
    level = getattr(logging, log_level, logging.WARNING)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    queue_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(queue_handler)
    listener.start()


@atexit.register
def stop_logging():
    listener.stop()


def run():
    parser = argparse.ArgumentParser(
        description="Process a project specification.", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "-c", "--config", type=Path, help="Path to the project specification file.", default="spec.yaml"
    )
    parent_parser.add_argument("-z", "--frozen", type=Path, help="Path to the frozen specification file.")
    parent_parser.add_argument(
        "-o",
        "--config-override",
        type=Path,
        help="Path to an override config to extend the project specification.",
    )
    parent_parser.add_argument("-j", "--concurrency", type=int, default=28, help="Number of concurrent tasks.")
    parent_parser.add_argument(
        "-b",
        "--use-bindfs",
        action="store_true",
        help="Use bindfs instead of creating symlinks (must have user_allow_other in /etc/fuse.conf).",
    )
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
    clean_parser.add_argument(
        "--dirty",
        action="store_true",
        help="Additionally check all repos for dirty state and offer to reset them with 'git reset --hard'.",
    )

    args = parser.parse_args()

    level_name = args.log_level
    setup_logging(level_name)

    project_spec = load_spec_file(args.config, args.frozen, args.workdir, args.config_override)
    if project_spec is None:
        sys.exit(1)

    try:
        if args.command == "freeze":
            asyncio.run(freeze_project(project_spec, args.frozen, concurrency=args.concurrency))
        elif args.command == "build":
            asyncio.run(process_project(project_spec, concurrency=args.concurrency, use_bindfs=args.use_bindfs))
        elif args.command == "clean":
            ret = clean_project(
                project_spec,
                non_interactive=args.i_am_stupid,
                clean_dirty_repos=args.dirty,
            )
            if ret != 0:
                sys.exit(1)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    run()
