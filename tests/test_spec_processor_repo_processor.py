from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import _make_ref, _make_repo_info, _make_repo_processor


def test_repo_processor_count_step_and_grouping(tmp_path: Path) -> None:
    refspecs = [
        _make_ref("origin", "main"),
        _make_ref("origin", "feature"),
        _make_ref("other", "dev"),
    ]
    remotes = {"origin": "https://example.com/repo.git", "other": "https://example.com/other.git"}
    shell_cmds = ["echo test", "git status"]
    patches = ["*.patch", "extra/*.patch"]
    repo_info = _make_repo_info(
        modules=["m1", "m2"],
        remotes=remotes,
        refspecs=refspecs,
        shell_commands=shell_cmds,
        patch_globs_to_apply=patches,
    )

    rp = _make_repo_processor(tmp_path, repo_info)

    expected = 1 + len(remotes) + (len(refspecs) - 1) + len(shell_cmds) + len(patches) + 1
    assert rp.count_step() == expected

    grouped = rp.get_refspec_by_remote()
    assert set(grouped.keys()) == {"origin", "other"}
    assert [r.refspec for r in grouped["origin"]] == ["main", "feature"]
    assert [r.refspec for r in grouped["other"]] == ["dev"]


def test_filter_non_link_module(tmp_path: Path) -> None:
    links_dir = tmp_path / "links"
    links_dir.mkdir()

    repo_info = _make_repo_info(modules=["symlinked", "regular", "missing", "empty"])
    rp = _make_repo_processor(tmp_path, repo_info)

    src_symlink_dir = tmp_path / "src_symlink"
    src_symlink_dir.mkdir()
    (links_dir / "symlinked").symlink_to(src_symlink_dir, target_is_directory=True)

    (links_dir / "empty").mkdir()
    regular = links_dir / "regular"
    regular.mkdir()
    (regular / "file.txt").write_text("content")

    result = rp.filter_non_link_module(repo_info)
    assert set(result) == {"symlinked", "missing", "empty"}


class TestLocalPath:
    def test_local_path_module_in_list(self, tmp_path: Path) -> None:
        local_paths = {"/custom/modules": ["mod1", "mod2"]}
        repo_info = _make_repo_info(modules=["mod1", "mod2"], paths=local_paths)
        rp = _make_repo_processor(tmp_path, repo_info)

        result = rp.local_path("mod1", local_paths)
        assert result is not None
        assert result == (tmp_path / "/custom/modules" / "mod1").resolve()

    def test_local_path_empty_catch_all(self, tmp_path: Path) -> None:
        local_paths = {"/custom/modules": []}
        repo_info = _make_repo_info(modules=["mod1"], paths=local_paths)
        rp = _make_repo_processor(tmp_path, repo_info)

        result = rp.local_path("any_module", local_paths)
        assert result is not None
        assert result == (tmp_path / "/custom/modules" / "any_module").resolve()

    def test_local_path_module_substitution(self, tmp_path: Path) -> None:
        local_paths = {"$MODULE/local": ["mod1"]}
        repo_info = _make_repo_info(modules=["mod1"], paths=local_paths)
        rp = _make_repo_processor(tmp_path, repo_info)

        result = rp.local_path("mod1", local_paths)
        assert result is not None
        assert result == (tmp_path / "mod1/local" / "mod1").resolve()

    def test_local_path_returns_none_when_not_found(self, tmp_path: Path) -> None:
        local_paths = {"/custom/modules": ["mod1", "mod2"]}
        repo_info = _make_repo_info(modules=["mod1", "mod2"], paths=local_paths)
        rp = _make_repo_processor(tmp_path, repo_info)

        result = rp.local_path("mod3", local_paths)
        assert result is None


class TestFilterLocalModule:
    def test_filter_local_module_excludes_existing(self, tmp_path: Path) -> None:
        local_paths = {"custom_modules": ["local_mod"]}
        (tmp_path / "custom_modules" / "local_mod").mkdir(parents=True)
        (tmp_path / "custom_modules" / "local_mod" / "file.py").write_text("# local module")

        repo_info = _make_repo_info(modules=["local_mod", "remote_mod"], paths=local_paths)
        rp = _make_repo_processor(tmp_path, repo_info)

        result = rp.filter_local_module(["local_mod", "remote_mod"], local_paths, {})
        assert "local_mod" not in result
        assert "remote_mod" in result

    def test_filter_local_module_includes_missing_with_warning(self, tmp_path: Path) -> None:
        local_paths = {"/custom_modules": ["missing_mod"]}
        repo_info = _make_repo_info(modules=["missing_mod"], paths=local_paths)
        rp = _make_repo_processor(tmp_path, repo_info)

        result = rp.filter_local_module(["missing_mod"], local_paths, {})
        assert "missing_mod" in result

    def test_filter_local_module_no_local_paths(self, tmp_path: Path) -> None:
        repo_info = _make_repo_info(modules=["mod1", "mod2"])
        rp = _make_repo_processor(tmp_path, repo_info)

        result = rp.filter_local_module(["mod1", "mod2"], {}, {})
        assert result == ["mod1", "mod2"]


@pytest.mark.asyncio
async def test_link_all_modules_with_local_paths(monkeypatch, tmp_path: Path) -> None:
    local_modules_path = tmp_path / "local_modules"
    local_modules_path.mkdir()
    (local_modules_path / "local_mod").mkdir()
    (local_modules_path / "local_mod" / "file.py").write_text("# local")

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "remote_mod").mkdir()
    (repo_path / "remote_mod" / "file.py").write_text("# remote")

    local_paths = {"local_modules": ["local_mod"]}
    repo_info = _make_repo_info(
        modules=["local_mod", "remote_mod"],
        paths=local_paths,
    )
    rp = _make_repo_processor(tmp_path, repo_info)
    rp.task_id = 0

    async def fake_run(*args, **kwargs):
        return 0, "", ""

    monkeypatch.setattr("bl.spec_processor.run", fake_run)

    ret, err = await rp.link_all_modules(["local_mod", "remote_mod"], repo_path, local_paths)
    assert ret == 0

    links_dir = tmp_path / "links"
    assert (links_dir / "local_mod").is_symlink()
    assert (links_dir / "remote_mod").is_symlink()
    assert (links_dir / "local_mod").resolve() == local_modules_path / "local_mod"
