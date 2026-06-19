from __future__ import annotations

from configparser import ConfigParser

from bl import config as bl_config


def test_load_config_creates_project_config_file(monkeypatch, tmp_path):
    monkeypatch.setattr(bl_config, "xdg_config_home", lambda: tmp_path)

    parser = bl_config.load_config("my-project")

    assert isinstance(parser, ConfigParser)
    assert (tmp_path / "bl" / "my-project" / "config.ini").exists()


def test_write_config_creates_and_persists_config(monkeypatch, tmp_path):
    monkeypatch.setattr(bl_config, "xdg_config_home", lambda: tmp_path)
    parser = ConfigParser()
    parser["editable"] = {"repo-a": "True"}

    bl_config.write_config("my-project", parser)

    reloaded = bl_config.load_config("my-project")

    assert reloaded["editable"]["repo-a"] == "True"
