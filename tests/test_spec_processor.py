from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List

import pytest

from bl.spec_processor import (
    RepoProcessor,
    path_is_not_repo,
    clone_info_from_repo,
    create_clone_args,
    normalize_merge_result,
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
) -> RepoInfo:
    return RepoInfo(
        modules=modules or [],
        remotes=remotes or {},
        refspecs=refspecs or [],
        shell_commands=shell_commands or [],
        patch_globs_to_apply=patch_globs_to_apply or [],
        target_folder=None,
        locales=locales or [],
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
