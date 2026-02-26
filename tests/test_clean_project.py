from __future__ import annotations

from pathlib import Path

import pytest

from bl.clean_project import _clean_directory, clean_project
from bl.types import ProjectSpec


def test_clean_directory_non_interactive_missing_dir(tmp_path: Path) -> None:
    target = tmp_path / "missing"
    failed = _clean_directory(target, non_interactive=True)
    assert failed is False
    # Directory should still not exist
    assert not target.exists()


def test_clean_directory_non_interactive_success(tmp_path: Path) -> None:
    target = tmp_path / "to_delete"
    target.mkdir()
    failed = _clean_directory(target, non_interactive=True)
    assert failed is False
    assert not target.exists()


def test_clean_directory_non_interactive_failure(monkeypatch, tmp_path: Path) -> None:
    import shutil

    target = tmp_path / "to_delete"
    target.mkdir()

    def fake_rmtree(path: Path) -> None:
        raise OSError("boom")

    monkeypatch.setattr(shutil, "rmtree", fake_rmtree)

    failed = _clean_directory(target, non_interactive=True)
    assert failed is True
    # Directory may or may not remain, but we only care about failure flag here.


def test_clean_directory_interactive_yes(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "to_delete"
    target.mkdir()

    monkeypatch.setattr("builtins.input", lambda _: "y")

    failed = _clean_directory(target, non_interactive=False)
    assert failed is False
    assert not target.exists()


def test_clean_directory_interactive_no(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "to_keep"
    target.mkdir()

    monkeypatch.setattr("builtins.input", lambda _: "n")

    failed = _clean_directory(target, non_interactive=False)
    assert failed is False
    # Directory should not be deleted
    assert target.exists()


def test_clean_directory_interactive_error_after_confirmation(monkeypatch, tmp_path: Path) -> None:
    import shutil

    target = tmp_path / "to_delete"
    target.mkdir()

    monkeypatch.setattr("builtins.input", lambda _: "y")

    def failing_rmtree(path: Path) -> None:
        raise OSError("boom")

    monkeypatch.setattr(shutil, "rmtree", failing_rmtree)

    failed = _clean_directory(target, non_interactive=False)
    assert failed is True


def test_clean_project_non_interactive_removes_dirs(tmp_path: Path) -> None:
    workdir = tmp_path
    src = workdir / "src"
    external = workdir / "external-src"
    src.mkdir()
    external.mkdir()

    project = ProjectSpec(repos={}, workdir=workdir)
    ret = clean_project(project, non_interactive=True)
    assert ret == 0
    assert not src.exists()
    assert not external.exists()


def test_clean_project_non_interactive_partial_failure(monkeypatch, tmp_path: Path) -> None:
    import shutil

    workdir = tmp_path
    src = workdir / "src"
    external = workdir / "external-src"
    src.mkdir()
    external.mkdir()

    real_rmtree = shutil.rmtree

    def fake_rmtree(path: Path) -> None:
        if path == src.resolve():
            raise OSError("boom")
        real_rmtree(path)

    monkeypatch.setattr(shutil, "rmtree", fake_rmtree)

    project = ProjectSpec(repos={}, workdir=workdir)
    ret = clean_project(project, non_interactive=True)
    # One deletion failed -> clean_project should signal failure
    assert ret == 1

