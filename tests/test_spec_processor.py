from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List

import pytest

from bl.spec_processor import (
    RepoProcessor,
    clone_info_from_repo,
    create_clone_args,
    normalize_merge_result,
    parse_fetch_output,
    path_is_not_repo,
    print_fetch_output,
)
from bl.types import CloneFlags, OriginType, ProjectSpec, RefspecInfo, RepoInfo


def _make_ref(remote: str, refspec: str, type_: OriginType = OriginType.BRANCH) -> RefspecInfo:
    return RefspecInfo(remote, refspec, type_, None)


def _make_repo_info(
    modules: List[str] | None = None,
    remotes: dict[str, str] | None = None,
    refspecs: List[RefspecInfo] | None = None,
    shell_commands: List[str] | None = None,
    patch_globs_to_apply: List[str] | None = None,
    locales: List[str] | None = None,
    paths: dict[str, List[str]] | None = None,
) -> RepoInfo:
    return RepoInfo(
        modules=modules or [],
        remotes=remotes or {},
        refspecs=refspecs or [],
        shell_commands=shell_commands or [],
        patch_globs_to_apply=patch_globs_to_apply or [],
        target_folder=None,
        locales=locales or [],
        paths=paths or {},
    )


def _make_repo_processor(tmp_path: Path, repo_info: RepoInfo) -> RepoProcessor:
    # Provide a minimal progress object so methods that update progress don't crash.
    class DummyProgress:
        def update(self, *args, **kwargs) -> None:
            return None

        def advance(self, *args, **kwargs) -> None:
            return None

        def add_task(self, *args, **kwargs) -> int:
            return 0

        def remove_task(self, *args, **kwargs) -> None:
            return None

    semaphore = pytest.importorskip("asyncio").Semaphore(1)
    dummy_progress = DummyProgress()
    dummy_count_progress = DummyProgress()
    dummy_task_id = 0
    return RepoProcessor(
        workdir=tmp_path,
        name="test-repo",
        semaphore=semaphore,
        repo_info=repo_info,
        progress=dummy_progress,
        count_progress=dummy_count_progress,
        count_task=dummy_task_id,
        concurrency=1,
    )


def test_check_path_is_repo(tmp_path: Path) -> None:
    # Non-existing path -> considered not a repo (returns True)
    missing = tmp_path / "missing"
    assert path_is_not_repo(missing) is True

    # Existing file -> not a directory -> considered not a repo
    file_path = tmp_path / "file.txt"
    file_path.write_text("content")
    assert path_is_not_repo(file_path) is True

    # Existing directory -> currently treated as an existing repo (returns False)
    dir_path = tmp_path / "dir"
    dir_path.mkdir()
    assert path_is_not_repo(dir_path) is False


def test_clone_info_from_repo_flags_variants() -> None:
    # Non-odoo, multiple refspecs, no locales -> SPARSE only
    refspecs = [_make_ref("origin", "main"), _make_ref("origin", "feature")]
    repo_info = _make_repo_info(
        modules=[],
        remotes={"origin": "https://example.com/repo.git"},
        refspecs=refspecs,
    )
    ci = clone_info_from_repo("addons", repo_info)
    assert ci.url == "https://example.com/repo.git"
    assert ci.root_refspec_info is refspecs[0]
    assert ci.clone_flags & CloneFlags.SHALLOW == 0
    assert ci.clone_flags & CloneFlags.SPARSE

    # odoo, single refspec -> SHALLOW only
    refspecs = [_make_ref("origin", "main")]
    repo_info = _make_repo_info(
        modules=[],
        remotes={"origin": "https://example.com/odoo.git"},
        refspecs=refspecs,
    )
    ci = clone_info_from_repo("odoo", repo_info)
    assert ci.clone_flags & CloneFlags.SHALLOW
    assert ci.clone_flags & CloneFlags.SPARSE == 0

    # Non-odoo, single refspec but with locales -> SHALLOW + SPARSE
    refspecs = [_make_ref("origin", "main")]
    repo_info = _make_repo_info(
        modules=[],
        remotes={"origin": "https://example.com/repo.git"},
        refspecs=refspecs,
        locales=["fr_FR"],
    )
    ci = clone_info_from_repo("addons", repo_info)
    assert ci.clone_flags & CloneFlags.SHALLOW
    assert ci.clone_flags & CloneFlags.SPARSE


def test_create_clone_args_for_ref_and_branch() -> None:
    # REF origin type
    ref = _make_ref("origin", "abcdef", OriginType.REF)
    from bl.types import CloneInfo as CloneInfoType

    ci_ref = CloneInfoType(
        url="https://example.com/repo.git",
        clone_flags=int(CloneFlags.SHALLOW | CloneFlags.SPARSE),
        root_refspec_info=ref,
    )
    args_ref = create_clone_args(ci_ref)
    assert "clone" in args_ref
    assert "--depth" in args_ref and "1" in args_ref
    assert "--sparse" in args_ref
    assert "--revision" in args_ref
    assert "abcdef" in args_ref
    assert args_ref[-1] == "https://example.com/repo.git"

    # BRANCH origin type
    br = _make_ref("origin", "main", OriginType.BRANCH)
    ci_branch = CloneInfoType(
        url="https://example.com/repo.git",
        clone_flags=0,
        root_refspec_info=br,
    )
    args_br = create_clone_args(ci_branch)
    assert "--origin" in args_br
    assert "origin" in args_br
    assert "--branch" in args_br
    assert "main" in args_br


def test_normalize_merge_result_conflict_and_success() -> None:
    ret, msg = normalize_merge_result(0, "Auto-merging\nCONFLICT (content):", "")
    assert ret == -1
    assert "CONFLICT" in msg

    ret2, msg2 = normalize_merge_result(1, "All good", "some error")
    assert ret2 == 1
    assert msg2 == "some error"


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

    # count_step formula:
    # 1 clone + len(remotes) fetches + (len(refspec_info)-1) merges
    # + len(shell_commands) + len(patch_globs_to_apply) + 1 link
    expected = 1 + len(remotes) + (len(refspecs) - 1) + len(shell_cmds) + len(patches) + 1
    assert rp.count_step() == expected

    grouped = rp.get_refspec_by_remote()
    assert set(grouped.keys()) == {"origin", "other"}
    assert [r.refspec for r in grouped["origin"]] == ["main", "feature"]
    assert [r.refspec for r in grouped["other"]] == ["dev"]


def test_filter_non_link_module(tmp_path: Path) -> None:
    links_dir = tmp_path / "links"
    links_dir.mkdir()

    # Set up modules:
    # - "symlinked": target is symlink -> included
    # - "regular": target is regular dir -> excluded
    # - "missing": no target -> included
    # - "empty": target is empty dir -> included
    repo_info = _make_repo_info(modules=["symlinked", "regular", "missing", "empty"])
    rp = _make_repo_processor(tmp_path, repo_info)

    src_symlink_dir = tmp_path / "src_symlink"
    src_symlink_dir.mkdir()
    (links_dir / "symlinked").symlink_to(src_symlink_dir, target_is_directory=True)

    (links_dir / "empty").mkdir()
    regular = links_dir / "regular"
    regular.mkdir()
    # Add a file in regular
    (regular / "file.txt").write_text("content")

    result = rp.filter_non_link_module(repo_info)
    assert set(result) == {"symlinked", "missing", "empty"}


@pytest.mark.asyncio
async def test_fetch_multi_builds_correct_args(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    calls: list[tuple[tuple[str, ...], Path | None]] = []

    async def fake_run_git(*args: str, cwd: Path | None = None):
        calls.append((args, cwd))
        return 0, "", ""

    def fake_get_local_ref(refspec_info: RefspecInfo) -> str:
        return f"local/{refspec_info.refspec}"

    monkeypatch.setattr(sp, "run_git", fake_run_git)
    monkeypatch.setattr(sp, "get_local_ref", fake_get_local_ref)

    ref1 = _make_ref("origin", "main")
    ref2 = _make_ref("origin", "feature")

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    module_path = tmp_path / "repo"
    module_path.mkdir()

    ret, out, err = await rp.fetch_multi("origin", [ref1, ref2], module_path)
    assert ret == 0
    assert out == ""
    assert err == ""

    assert len(calls) == 1
    args, cwd = calls[0]
    assert args[0] == "fetch"
    assert "origin" in args
    assert "main:local/main" in args
    assert "feature:local/feature" in args
    assert cwd == module_path


@pytest.mark.asyncio
async def test_check_and_apply_patch_paths_and_flows(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    module_path = tmp_path / "repo"
    module_path.mkdir()
    patch_file = module_path / "patches" / "fix.patch"
    patch_file.parent.mkdir()
    patch_file.write_text("dummy patch")

    calls: list[tuple[tuple[str, ...], Path | None]] = []

    async def fake_run_git(*args, cwd: Path | None = None):
        calls.append((args, cwd))
        if args[0] == "apply":
            # Simulate: reverse check indicates patch not already applied (non-zero)
            return 1, "", "needs am"
        if args[0] == "am":
            # Simulate git am success regardless of exact argument type
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    ret, err = await rp.check_and_apply_patch("patches/*.patch", module_path)
    # We expect success when git am succeeds
    assert ret == 0
    assert err == ""

    # We expect two git commands: apply --reverse --check, and am
    assert any(call[0][0] == "apply" for call in calls)
    assert any(call[0][0] == "am" for call in calls)


@pytest.mark.asyncio
async def test_setup_sparse_checkout_and_odoo(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    seen_calls: list[tuple[tuple[str, ...], Path | None]] = []

    async def fake_run_git(*args: str, cwd: Path | None = None):
        seen_calls.append((args, cwd))
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    # Non-odoo repo: should call sparse-checkout set with modules from repo_info
    non_odoo_repo = _make_repo_info(modules=["m1", "m2"])
    rp_non_odoo = _make_repo_processor(tmp_path, non_odoo_repo)
    rp_non_odoo.task_id = 0
    module_path = tmp_path / "non_odoo"
    module_path.mkdir()

    await rp_non_odoo.setup_sparse_checkout(["m1", "m2"], module_path)
    assert any(
        call[0][0] == "sparse-checkout" and call[0][1] == "set" and set(call[0][2:]) == {"m1", "m2"}
        for call in seen_calls
    )

    # Clear calls and test odoo-specific sparse setup
    seen_calls.clear()

    odoo_repo = _make_repo_info(modules=["mod1"], locales=["fr_FR", "en_US"])
    rp_odoo = _make_repo_processor(tmp_path, odoo_repo)
    rp_odoo.name = "odoo"
    rp_odoo.task_id = 0
    odoo_path = tmp_path / "odoo"
    odoo_path.mkdir()

    await rp_odoo.setup_sparse_checkout(["mod1"], odoo_path)

    # We expect a sparse-checkout init and then a set with the right patterns
    assert any(call[0][0:3] == ("sparse-checkout", "init", "--no-cone") for call in seen_calls)
    assert any(
        call[0][0:2] == ("sparse-checkout", "set")
        and "/*" in call[0]
        and "!/addons/*" in call[0]
        and "/addons/mod1/*" in call[0]
        and "!*.po" in call[0]
        and "fr_FR.po" in call[0]
        and "en_US.po" in call[0]
        for call in seen_calls
    )


@pytest.mark.asyncio
async def test_process_project_minimal_integration(tmp_path: Path) -> None:
    """Very small integration: ensure process_project clones from a simple local repo."""
    from bl.spec_parser import load_spec_file
    from bl.spec_processor import process_project
    from tests.test_frozen_processor import _init_test_repo, _run_git

    with TemporaryDirectory() as td:
        td_path = Path(td)
        remote_repo, _, head_sha = _init_test_repo(td_path)

        workdir = td_path / "workdir"
        workdir.mkdir()

        # Minimal spec without frozen.yaml
        spec_data = {
            "test-module": {
                "modules": [],
                "remotes": {
                    "origin": str(remote_repo),
                },
                "merges": [
                    "origin main",
                ],
            }
        }

        spec_path = workdir / "spec.yaml"
        spec_path.write_text(__import__("yaml").safe_dump(spec_data))

        project = load_spec_file(spec_path, None, workdir)
        assert isinstance(project, ProjectSpec)

        await process_project(project, concurrency=1)

        module_repo = workdir / "external-src" / "test-module"
        assert module_repo.is_dir()
        current_head = _run_git(module_repo, "rev-parse", "HEAD")
        assert current_head == head_sha


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

        result = rp.filter_local_module(["local_mod", "remote_mod"], local_paths)
        assert "local_mod" not in result
        assert "remote_mod" in result

    def test_filter_local_module_includes_missing_with_warning(self, tmp_path: Path) -> None:
        local_paths = {"/custom_modules": ["missing_mod"]}
        repo_info = _make_repo_info(modules=["missing_mod"], paths=local_paths)
        rp = _make_repo_processor(tmp_path, repo_info)

        result = rp.filter_local_module(["missing_mod"], local_paths)
        assert "missing_mod" in result

    def test_filter_local_module_no_local_paths(self, tmp_path: Path) -> None:
        repo_info = _make_repo_info(modules=["mod1", "mod2"])
        rp = _make_repo_processor(tmp_path, repo_info)

        result = rp.filter_local_module(["mod1", "mod2"], {})
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


def test_clone_info_from_repo_odoo_with_locales() -> None:
    """odoo with locales should have both SHALLOW and SPARSE flags."""
    refspecs = [_make_ref("origin", "main")]
    repo_info = _make_repo_info(
        modules=[],
        remotes={"origin": "https://example.com/odoo.git"},
        refspecs=refspecs,
        locales=["fr_FR"],
    )
    ci = clone_info_from_repo("odoo", repo_info)
    assert ci.clone_flags & CloneFlags.SHALLOW
    assert ci.clone_flags & CloneFlags.SPARSE


@pytest.mark.asyncio
async def test_setup_new_repo_clone_failure(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    rp.task_id = 0

    async def fake_run_git(*args, cwd=None):
        return 1, "", "clone failed"

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    ref = _make_ref("origin", "main")
    clone_info = type("CloneInfo", (), {"url": "https://example.com", "root_refspec_info": ref, "clone_flags": 0})()

    ret, err = await rp.setup_new_repo(clone_info, tmp_path / "module")
    assert ret == 1
    assert "Clone failed" in err


@pytest.mark.asyncio
async def test_reset_repo_for_work_dirty_repo(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    module_path = tmp_path / "module"
    module_path.mkdir()

    async def fake_run_git(*args, cwd=None):
        if "status" in args:
            return 0, " M file.txt", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    ret, err = await rp.reset_repo_for_work(module_path)
    assert ret == -1
    assert "Repo is dirty" in err


@pytest.mark.asyncio
async def test_reset_repo_for_work_not_exists(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    module_path = tmp_path / "nonexistent"

    async def fake_run_git(*args, cwd=None):
        return 1, "", "fatal: not a repo"

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    ret, err = await rp.reset_repo_for_work(module_path)
    assert ret == 1
    assert "Repo does not exist" in err


def test_rich_warning() -> None:
    from bl.spec_processor import rich_warning

    rich_warning("test message", UserWarning, "test.py", 10)


def test_parse_fetch_output_empty() -> None:
    result = parse_fetch_output("")
    assert result == []


def test_parse_fetch_output_fast_forward() -> None:
    output = " abc123 def456 refs/heads/main"
    result = parse_fetch_output(output)
    assert len(result) == 1
    assert result[0]["base"] == "abc123"
    assert result[0]["target"] == "def456"


def test_parse_fetch_output_tag() -> None:
    output = "tag abc123 def456 refs/heads/main"
    result = parse_fetch_output(output)
    assert len(result) == 1
    assert result[0]["base"] == "abc123"
    assert result[0]["target"] == "def456"


def test_parse_fetch_output_skips_zero_hash() -> None:
    output = " abc000000000 def456 refs/heads/main"
    result = parse_fetch_output(output)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_check_main_remote_multiple_remotes_returns_early(tmp_path: Path) -> None:
    """With multiple remotes, should return early with 0."""
    refspecs = [_make_ref("origin", "main")]
    repo_info = _make_repo_info(remotes={"origin": "url1", "upstream": "url2"}, refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)

    module_path = tmp_path / "repo"
    module_path.mkdir()

    ret, err = await rp.check_main_remote(module_path)
    assert ret == 0


@pytest.mark.asyncio
async def test_check_main_remote_url_mismatch(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    refspecs = [_make_ref("origin", "main")]
    repo_info = _make_repo_info(remotes={"origin": "https://new-url.com/repo.git"}, refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)

    module_path = tmp_path / "repo"
    module_path.mkdir()

    call_count = 0

    async def fake_run_git(*args, cwd=None):
        nonlocal call_count
        call_count += 1
        if "get-url" in args:
            return 0, "https://old-url.com/repo.git", ""
        if "remove" in args:
            return 0, "", ""
        if "add" in args:
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    ret, err = await rp.check_main_remote(module_path)
    assert ret == 0
    assert call_count >= 3


@pytest.mark.asyncio
async def test_checkout_or_create_base_branch_remote_branch(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    refspecs = [_make_ref("origin", "feature")]
    repo_info = _make_repo_info(refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)

    module_path = tmp_path / "repo"
    module_path.mkdir()

    call_count = 0

    async def fake_run_git(*args, cwd=None):
        nonlocal call_count
        call_count += 1
        if "rev-parse" in args:
            if "--verify" in args:
                if "origin/feature" in args:
                    return 0, "", ""
                return 1, "", "not found"
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    base_refspec = refspecs[0]
    ret, out, err = await rp.checkout_or_create_base_branch(base_refspec, module_path)
    assert ret == 0


@pytest.mark.asyncio
async def test_checkout_or_create_base_branch_not_found(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    refspecs = [_make_ref("origin", "nonexistent")]
    repo_info = _make_repo_info(refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)

    module_path = tmp_path / "repo"
    module_path.mkdir()

    async def fake_run_git(*args, cwd=None):
        if "rev-parse" in args:
            return 1, "", "not found"
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    base_refspec = refspecs[0]
    ret, out, err = await rp.checkout_or_create_base_branch(base_refspec, module_path)
    assert ret == -1
    assert "Can't find base branch" in err


@pytest.mark.asyncio
async def test_merge_spec_into_tree_conflict(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    refspecs = [_make_ref("origin", "feature")]
    repo_info = _make_repo_info(refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()

    async def fake_run_git(*args, cwd=None):
        if "merge" in args:
            return 1, "CONFLICT in file.txt", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    ref = refspecs[0]
    root_ref = _make_ref("origin", "main")
    ret, err = await rp.merge_spec_into_tree(repo_info, ref, root_ref, module_path)
    assert ret == -1
    assert "CONFLICT" in err


@pytest.mark.asyncio
async def test_merge_spec_into_tree_error(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp
    from bl.spec_processor import console

    refspecs = [_make_ref("origin", "feature")]
    repo_info = _make_repo_info(refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()

    printed = []
    monkeypatch.setattr(console, "print", lambda x: printed.append(str(x)))

    async def fake_run_git(*args, cwd=None):
        if "merge" in args:
            return 1, "", "merge failed"
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    ref = refspecs[0]
    root_ref = _make_ref("origin", "main")
    ret, err = await rp.merge_spec_into_tree(repo_info, ref, root_ref, module_path)
    assert ret == 1


@pytest.mark.asyncio
async def test_check_and_apply_patch_no_files(monkeypatch, tmp_path: Path) -> None:
    """When no patch files exist, should return error and print message."""
    from bl import spec_processor as sp
    from bl.spec_processor import console

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()

    printed = []
    monkeypatch.setattr(console, "print", lambda x: printed.append(str(x)))

    ret, err = await rp.check_and_apply_patch("nonexistent/*.patch", module_path)
    assert ret == -1


@pytest.mark.asyncio
async def test_check_and_apply_patch_already_applied(monkeypatch, tmp_path: Path) -> None:
    """When patch is already applied, should return 0 (no action needed)."""
    from bl import spec_processor as sp

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()
    (module_path / "patches").mkdir()
    (module_path / "patches" / "fix.patch").write_text("patch")

    async def fake_run_git(*args, cwd=None):
        if "apply" in args and "--reverse" in args:
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    ret, err = await rp.check_and_apply_patch("patches/*.patch", module_path)
    assert ret == 0


class TestParseFetchOutput:
    def test_parse_fetch_output_empty(self) -> None:
        result = parse_fetch_output("")
        assert result == []

    def test_parse_fetch_output_none(self) -> None:
        result = parse_fetch_output(None)
        assert result == []

    def test_parse_fetch_output_four_elements(self) -> None:
        output = "f abc123 def456 refs/heads/main"
        result = parse_fetch_output(output)
        assert len(result) == 1
        assert result[0]["base"] == "abc123"
        assert result[0]["target"] == "def456"
        assert result[0]["ref"] == "main"

    def test_parse_fetch_output_five_elements(self) -> None:
        output = "  abc123 def456 refs/heads/feature"
        result = parse_fetch_output(output)
        assert len(result) == 1
        assert result[0]["base"] == "abc123"
        assert result[0]["target"] == "def456"
        assert result[0]["ref"] == "feature"

    def test_parse_fetch_output_skips_000_base(self) -> None:
        output = "f 000000000000000000000000000000000000000 def456 refs/heads/main"
        result = parse_fetch_output(output)
        assert result == []

    def test_parse_fetch_output_multiple_lines(self) -> None:
        output = "f abc123 def456 refs/heads/main\nf ghi789 jkl012 refs/heads/develop"
        result = parse_fetch_output(output)
        assert len(result) == 2
        assert result[0]["ref"] == "main"
        assert result[1]["ref"] == "develop"

    def test_parse_fetch_output_strips_ref_prefix(self) -> None:
        output = "f abc123 def456 refs/heads/feature/my-branch"
        result = parse_fetch_output(output)
        assert result[0]["ref"] == "feature/my-branch"

    def test_parse_fetch_output_invalid_line_count(self) -> None:
        output = "only one element"
        result = parse_fetch_output(output)
        assert result == []

    def test_parse_fetch_output_three_elements(self) -> None:
        output = "abc def ghi"
        result = parse_fetch_output(output)
        assert result == []


@pytest.mark.asyncio
async def test_print_fetch_output(monkeypatch, tmp_path: Path) -> None:
    from bl.spec_processor import console

    module_path = tmp_path / "repo"
    module_path.mkdir()

    printed = []
    monkeypatch.setattr(console, "print", lambda x: printed.append(str(x)))

    async def fake_run_git(*args, cwd=None):
        if "log" in args:
            return 0, "abc123|Author|Commit message\n", ""
        return 0, "", ""

    monkeypatch.setattr("bl.spec_processor.run_git", fake_run_git)

    fetch_data = {
        "base": "abc123def456789012345678901234567890ab",
        "target": "def456789012345678901234567890abc123de",
        "ref": "main",
    }

    await print_fetch_output("test-repo", fetch_data, module_path)

    assert len(printed) == 2
    assert "test-repo" in printed[0]
    assert "abc123def" in printed[0]
    assert "def456789" in printed[0]
    assert "main" in printed[0]
    assert "abc123" in printed[1]
    assert "Author" in printed[1]


@pytest.mark.asyncio
async def test_process_repo_no_refspec_no_paths_returns_error(tmp_path: Path) -> None:
    """When repo has no refspec_info and no paths, should return -1."""
    rp = _make_repo_processor(tmp_path, _make_repo_info())
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()

    ret = await rp.process_repo(module_path, [], [])
    assert ret == -1


@pytest.mark.asyncio
async def test_run_shell_commands_with_regular_command(monkeypatch, tmp_path: Path) -> None:
    """Test shell command with regular (non-git-am) commands."""
    rp = _make_repo_processor(tmp_path, _make_repo_info(shell_commands=["echo test"]))
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()

    async def fake_run(*args, **kwargs):
        return 0, "", ""

    monkeypatch.setattr("bl.spec_processor.run", fake_run)

    ret = await rp.run_shell_commands(rp.repo_info, module_path)
    assert ret == 0


@pytest.mark.asyncio
async def test_run_shell_commands_failure(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    rp = _make_repo_processor(tmp_path, _make_repo_info(shell_commands=["false"]))
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()

    ret = await rp.run_shell_commands(rp.repo_info, module_path)
    assert ret == -1


@pytest.mark.asyncio
async def test_check_and_apply_patch_git_am_abort(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()
    (module_path / "patches").mkdir()
    (module_path / "patches" / "fix.patch").write_text("patch")

    async def fake_run_git(*args, cwd=None):
        if "apply" in args and "--reverse" in args:
            return 1, "", "needs apply"
        if "am" in args and "--abort" not in args:
            return 1, "", "am failed"
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    ret, err = await rp.check_and_apply_patch("patches/*.patch", module_path)
    assert ret == -1
