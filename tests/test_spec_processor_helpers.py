from __future__ import annotations

from pathlib import Path

from bl.spec_processor import (
    clone_info_from_repo,
    create_clone_args,
    normalize_merge_result,
    path_is_repo,
)
from bl.types import CloneFlags, OriginType
from tests.conftest import _make_ref, _make_repo_info


def test_check_path_is_repo(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert path_is_repo(missing) is False

    file_path = tmp_path / "file.txt"
    file_path.write_text("content")
    assert path_is_repo(file_path) is False

    dir_path = tmp_path / "dir"
    dir_path.mkdir()
    assert path_is_repo(dir_path) is False

    dir_path = tmp_path / "dir2"
    dir_path.mkdir()
    git_path = dir_path / ".git"
    git_path.mkdir()
    assert path_is_repo(dir_path) is True


def test_clone_info_from_repo_flags_variants() -> None:
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

    refspecs = [_make_ref("origin", "main")]
    repo_info = _make_repo_info(
        modules=[],
        remotes={"origin": "https://example.com/odoo.git"},
        refspecs=refspecs,
    )
    ci = clone_info_from_repo("odoo", repo_info)
    assert ci.clone_flags & CloneFlags.SHALLOW
    assert ci.clone_flags & CloneFlags.SPARSE

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


def test_clone_info_from_repo_odoo_with_locales() -> None:
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
