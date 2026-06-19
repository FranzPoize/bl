import argparse
import asyncio
import atexit
import json
import logging
import logging.handlers
import queue
import subprocess
import sys
from pathlib import Path

from copier import run_copy
from plumbum.lib import captured_stdout
from rich.console import Console

import bl
from bl.clean_project import clean_project, show_diffs
from bl.editable import make_editable
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


def check_last_version() -> bool:
    ret = subprocess.run(["which", "pip"], capture_output=True)
    ok = ret.returncode == 0
    pip_target = ret.stdout.decode().strip()

    pip_call = subprocess.run(["pip", "index", "versions", "--json", "bl-odoo"], capture_output=True)
    pip_index_ok = pip_call.returncode == 0
    pip_return_value = pip_call.stdout.decode().strip()

    json_pip_index = json.loads(pip_return_value)
    bl_last_version = json_pip_index["versions"][0]
    c_maj, c_min, c_patch = bl.__version__.split(".")
    l_maj, l_min, l_patch = bl_last_version.split(".")

    if c_maj < l_maj or c_min < l_min or c_patch < l_patch:
        msg = (
            "[red]Watch out ![/] There is a new bl version you should update "
            + f"(Yours is {bl.__version__} != Last is {bl_last_version})"
        )
        border_msg = "[red]" + "#" * (len(msg) - len("[red][/]")) + "[/]"
        out_console.print(f"{border_msg}\n{msg}\n{border_msg}\n\n")


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
    parent_parser.add_argument(
        "-N",
        "--no-check-version",
        action="store_true",
        help="Disable last version check",
    )
    parent_parser.add_argument("-z", "--frozen", type=Path, help="Path to the frozen specification file.")
    parent_parser.add_argument(
        "-o",
        "--config-override",
        type=Path,
        action="append",
        help="Path to an override config to extend the project specification. "
        "Can be used multiple times, applied in order.",
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
    sub.add_parser("diff", parents=[parent_parser], help="Show diff for all dirty repos")
    edit_parser = sub.add_parser("edit", parents=[parent_parser], help="Make a repo editable")
    edit_parser.add_argument("repository_name", type=Path)
    init_parser = sub.add_parser("init", parents=[parent_parser], help="Initialize a project from a template")
    init_parser.add_argument("destination", type=Path, nargs="?", default=Path("."), help="Destination directory")
    clean_parser = sub.add_parser("clean", parents=[parent_parser], help="Clean src and external-src in workdir")
    clean_parser.add_argument(
        "--remove",
        action="store_true",
        help="Delete src and external-src.",
    )
    clean_parser.add_argument(
        "--unlink",
        action="store_true",
        help="Clean the links directory.",
    )
    clean_parser.add_argument(
        "--force",
        action="store_true",
        help="Remove confirmation prompts.",
    )
    clean_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Just output dirty repo.",
    )

    args = parser.parse_args()

    level_name = args.log_level
    setup_logging(level_name)

    if not args.no_check_version:
        check_last_version()

    if args.command == "init":
        run_copy("https://github.com/akretion/docky-odoo-template-shared", args.destination)
        sys.exit(0)

    project_spec = load_spec_file(args.config, args.frozen, args.workdir, args.config_override)
    if project_spec is None:
        sys.exit(1)

    try:
        if args.command == "freeze":
            asyncio.run(freeze_project(project_spec, args.frozen, concurrency=args.concurrency))
        elif args.command == "build":
            asyncio.run(process_project(project_spec, concurrency=args.concurrency, use_bindfs=args.use_bindfs))
        elif args.command == "diff":
            asyncio.run(show_diffs(project_spec))
        elif args.command == "edit":
            asyncio.run(make_editable(args.repository_name, args.config, args.workdir))
        elif args.command == "clean":
            ret = asyncio.run(
                clean_project(
                    project_spec,
                    remove=args.remove,
                    unlink=args.unlink,
                    force=args.force,
                    dry_run=args.dry_run,
                )
            )
            if ret != 0:
                sys.exit(1)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    run()
