from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

from bl import config, spec_processor, utils
from bl.spec_parser import load_spec_file
from bl.spec_processor import process_project


@dataclass
class OdooProject:
    workdir: Path

    @property
    def source(self) -> Path:
        data = yaml.safe_load((self.workdir / "spec.yaml").read_text())
        return self.workdir / data["odoo"].get("target_folder", "src")

    def specification(self):
        spec = load_spec_file(self.workdir / "spec.yaml", None, self.workdir, [])
        assert spec is not None
        return spec

    def configure(self, **options) -> None:
        path = self.workdir / "spec.yaml"
        data = yaml.safe_load(path.read_text())
        data["odoo"].update(options)
        path.write_text(yaml.safe_dump(data))

    def pin(self, sha: str, *, alias: str = "origin", branch: str = "18.0") -> None:
        (self.workdir / "frozen.yaml").write_text(yaml.safe_dump({"odoo": {alias: {branch: sha}}}))

    async def build(self) -> None:
        await process_project(self.specification(), concurrency=1)


@dataclass
class OdooEnvironment:
    root: Path
    remote: Path
    env: dict[str, str] = field(repr=False)

    def git(self, repo: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, env=self.env, check=True, capture_output=True, text=True, timeout=15
        ).stdout.strip()

    def patch_series(self, *values: str) -> list[str]:
        """Generate real mail patches in the requested application order."""
        self.git(self.remote, "switch", "--detach", "18.0")
        patches = []
        try:
            for value in values:
                (self.remote / "odoo" / "message.txt").write_text(value + "\n")
                self.git(self.remote, "add", "odoo/message.txt")
                self.git(self.remote, "commit", "-m", f"Set message to {value}")
                patches.append(self.git(self.remote, "format-patch", "-1", "--stdout") + "\n")
        finally:
            self.git(self.remote, "switch", "18.0")
        return patches

    def project(self, name: str, patches: list[str] | None = None, prefix: str = "fix") -> OdooProject:
        workdir = self.root / name / "odoo"
        patch_dir = workdir / "patches"
        patch_dir.mkdir(parents=True)
        globs = []
        for index, patch in enumerate(patches or []):
            filename = f"{prefix}-{index}.patch"
            (patch_dir / filename).write_text(patch)
            # Existing spec paths are relative to the project-side src directory,
            # even when src will become a link into the shared store.
            globs.append(f"../patches/{filename}")
        (workdir / "spec.yaml").write_text(
            yaml.safe_dump(
                {"odoo": {"src": f"{self.remote.as_uri()} 18.0", "target_folder": "src", "patch_globs": globs}}
            )
        )
        return OdooProject(workdir)

    def advance(self, *, conflict: bool = False) -> str:
        self.git(self.remote, "switch", "18.0")
        (self.remote / "odoo" / "version.txt").write_text("new upstream\n")
        if conflict:
            (self.remote / "odoo" / "message.txt").write_text("conflicting upstream\n")
        self.git(self.remote, "add", "odoo")
        self.git(self.remote, "commit", "-m", "Advance 18.0")
        return self.git(self.remote, "rev-parse", "HEAD")

    @property
    def store_root(self) -> Path:
        return Path(self.env["XDG_DATA_HOME"]) / "bl" / "odoo"

    def common_dir(self, project: OdooProject) -> Path:
        return Path(self.git(project.source, "rev-parse", "--path-format=absolute", "--git-common-dir"))


def make_odoo_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OdooEnvironment:
    home = tmp_path / "home"
    home.mkdir()
    # BL captures its subprocess environment at import time, so isolate both
    # that snapshot and the live environment used to locate XDG directories.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_") and key != "BL_ODOO_STORE"}
    monkeypatch.delenv("BL_ODOO_STORE", raising=False)
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_DATA_HOME=str(home / "data"),
        XDG_CACHE_HOME=str(home / "cache"),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="BL Test",
        GIT_AUTHOR_EMAIL="bl-test@example.invalid",
        GIT_COMMITTER_NAME="BL Test",
        GIT_COMMITTER_EMAIL="bl-test@example.invalid",
        GIT_TERMINAL_PROMPT="0",
    )
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(utils, "bl_env", env)
    monkeypatch.setattr(spec_processor, "bl_env", env)
    monkeypatch.setattr(config, "xdg_config_home", lambda: home / "config")
    remote = tmp_path / "upstream"
    remote.mkdir()
    fixture = OdooEnvironment(tmp_path, remote, env)
    fixture.git(remote, "init", "-b", "18.0")
    fixture.git(remote, "config", "uploadpack.allowFilter", "true")
    (remote / "odoo").mkdir()
    (remote / "odoo" / "message.txt").write_text("original\n")
    (remote / "odoo" / "version.txt").write_text("old upstream\n")
    for name in ("account", "sale"):
        module = remote / "addons" / name
        (module / "i18n").mkdir(parents=True)
        (module / "__manifest__.py").write_text(repr({"name": name}) + "\n")
        for locale in ("fr", "es"):
            (module / "i18n" / f"{locale}.po").write_text(f"# {name}: {locale}\n")
    fixture.git(remote, "add", ".")
    fixture.git(remote, "commit", "-m", "Initial Odoo")
    return fixture
