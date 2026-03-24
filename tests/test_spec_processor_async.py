from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from tests.conftest import _make_ref, _make_repo_info, _make_repo_processor


@pytest.mark.asyncio
async def test_fetch_multi_builds_correct_args(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    calls: list[tuple[tuple[str, ...], Path | None]] = []

    async def fake_run_git(*args: str, cwd: Path | None = None):
        calls.append((args, cwd))
        return 0, "", ""

    def fake_get_local_ref(refspec_info) -> str:
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
            return 1, "", "needs am"
        if args[0] == "am":
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    ret, err = await rp.check_and_apply_patch("patches/*.patch", module_path)
    assert ret == 0
    assert err == ""

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

    seen_calls.clear()

    odoo_repo = _make_repo_info(modules=["mod1"], locales=["fr_FR", "en_US"])
    rp_odoo = _make_repo_processor(tmp_path, odoo_repo)
    rp_odoo.name = "odoo"
    rp_odoo.task_id = 0
    odoo_path = tmp_path / "odoo"
    odoo_path.mkdir()

    await rp_odoo.setup_sparse_checkout(["mod1"], odoo_path)

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
async def test_setup_new_repo_clone_failure(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp
    from bl.types import CloneInfo

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    rp.task_id = 0

    async def fake_run_git(*args, cwd=None):
        return 1, "", "clone failed"

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    ref = _make_ref("origin", "main")
    clone_info = CloneInfo(url="https://example.com", root_refspec_info=ref, clone_flags=0)

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


@pytest.mark.asyncio
async def test_check_main_remote_multiple_remotes_returns_early(tmp_path: Path) -> None:
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


@pytest.mark.asyncio
async def test_process_repo_no_refspec_no_paths_returns_error(tmp_path: Path) -> None:
    rp = _make_repo_processor(tmp_path, _make_repo_info())
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()

    ret = await rp.process_repo(module_path, [], [])
    assert ret == -1


@pytest.mark.asyncio
async def test_run_shell_commands_with_regular_command(monkeypatch, tmp_path: Path) -> None:
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
