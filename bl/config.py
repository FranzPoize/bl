from configparser import ConfigParser, SectionProxy
from os import makedirs
from pathlib import Path

from xdg_base_dirs import xdg_config_home

CONFIG_FILE_NAME = "config.ini"


def get_from_config(config: ConfigParser | SectionProxy | dict, *keys):
    current_config_section = config
    for key in keys:
        if key in current_config_section:
            current_config_section[key]
        else:
            return {}

    return current_config_section


def get_config_home(project_name: str) -> Path:
    return xdg_config_home() / "bl" / project_name


def get_config_file(project_name: str) -> Path:
    return get_config_home(project_name) / CONFIG_FILE_NAME


def create_project_config_file(project_name: str) -> None:
    project_config_home = get_config_home(project_name)
    makedirs(project_config_home, exist_ok=True)
    project_config_file = project_config_home / CONFIG_FILE_NAME
    project_config_file.touch(mode=0o764)


def load_config(project_name: str) -> ConfigParser:
    config_file = get_config_file(project_name)

    if not config_file.exists():
        create_project_config_file(project_name)

    parser = ConfigParser()

    parser.read(config_file)

    return parser


def write_config(project_name: str, config: ConfigParser) -> None:
    config_file = get_config_file(project_name)

    if not config_file.exists():
        create_project_config_file(project_name)

    with open(config_file, "w") as f:
        config.write(f)
