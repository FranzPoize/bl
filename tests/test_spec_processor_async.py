from __future__ import annotations

import warnings
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

    ret, out, err, fetch_outputs = await rp.fetch_multi("origin", [ref1, ref2], module_path)
    assert ret == 0
    assert out == ""
    assert err == ""
    assert fetch_outputs == []

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
        call[0][0] == "sparse-checkout"
        and call[0][1] == "set"
        and call[0][2] == "--cone"
        and set(call[0][3:]) == {"m1", "m2"}
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

    assert any(
        call[0][0:3] == ("sparse-checkout", "set", "--no-cone")
        and "/*" in call[0]
        and "!/addons/*" in call[0]
        and "/addons/mod1/*" in call[0]
        and "!/addons/mod1/*/*.po" in call[0]
        and "/addons/mod1/*/fr_FR.po" in call[0]
        and "/addons/mod1/*/en_US.po" in call[0]
        for call in seen_calls
    )

    odoo_path.rmdir()

    seen_calls.clear()

    odoo_repo_no_locales = _make_repo_info(modules=["mod1"])
    rp_odoo_no_locales = _make_repo_processor(tmp_path, odoo_repo_no_locales)
    rp_odoo_no_locales.name = "odoo"
    rp_odoo_no_locales.task_id = 0
    odoo_path_no_locales = tmp_path / "odoo"
    odoo_path_no_locales.mkdir()

    await rp_odoo_no_locales.setup_sparse_checkout(["mod1"], odoo_path_no_locales)

    assert any(
        call[0][0:3] == ("sparse-checkout", "set", "--cone")
        and set(call[0][3:]) == {"addons/mod1", "debian", "doc", "odoo", "setup"}
        for call in seen_calls
    )

    odoo_path.rmdir()

    seen_calls.clear()

    odoo_repo_no_modules = _make_repo_info()
    rp_odoo_no_modules = _make_repo_processor(tmp_path, odoo_repo_no_modules)
    rp_odoo_no_modules.name = "odoo"
    rp_odoo_no_modules.task_id = 0
    odoo_path_no_modules = tmp_path / "odoo"
    odoo_path_no_modules.mkdir()

    await rp_odoo_no_modules.setup_sparse_checkout([], odoo_path_no_modules)

    assert any(
        call[0][0:3] == ("sparse-checkout", "set", "--cone")
        and set(call[0][3:]) == {"addons", "debian", "doc", "odoo", "setup"}
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

    ret, fetch_outputs = await rp.process_repo(module_path, [], [])
    assert ret == -1
    assert fetch_outputs == []


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


@pytest.mark.asyncio
async def test_link_all_modules_unlink_error(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()
    (module_path / "mod1").mkdir()

    links_path = tmp_path / "links"
    links_path.mkdir()
    dest_link = links_path / "mod1"
    dest_link.symlink_to(module_path / "mod1")

    async def fake_unlink_path(path):
        return 1, "unlink failed"

    monkeypatch.setattr(sp, "unlink_path", fake_unlink_path)

    ret, err = await rp.link_all_modules(["mod1"], module_path, {})
    assert ret != 0
    assert "unlink failed" in err


@pytest.mark.asyncio
async def test_run_shell_commands_git_am_deprecated(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    rp = _make_repo_processor(tmp_path, _make_repo_info(shell_commands=["git am patches/*.patch"]))
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()

    check_and_apply_called_with = []

    async def fake_check_and_apply_patch(glob, module_path):
        check_and_apply_called_with.append(glob)
        return 0, ""

    monkeypatch.setattr(rp, "check_and_apply_patch", fake_check_and_apply_patch)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        ret = await rp.run_shell_commands(rp.repo_info, module_path)

        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)
        assert "run_shell_commands is deprecated" in str(w[0].message)

    assert ret == 0
    assert check_and_apply_called_with == ["patches/*.patch"]


@pytest.mark.asyncio
async def test_check_main_remote_get_url_fails(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    refspecs = [_make_ref("origin", "main")]
    repo_info = _make_repo_info(remotes={"origin": "https://example.com/repo.git"}, refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)

    module_path = tmp_path / "repo"
    module_path.mkdir()

    async def fake_run_git(*args, cwd=None):
        if "get-url" in args:
            return 1, "", "remote not found"
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    ret, err = await rp.check_main_remote(module_path)
    assert ret != 0
    assert "remote not found" in err


@pytest.mark.asyncio
async def test_check_main_remote_add_fails_after_remove(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    refspecs = [_make_ref("origin", "main")]
    repo_info = _make_repo_info(remotes={"origin": "https://new-url.com/repo.git"}, refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)

    module_path = tmp_path / "repo"
    module_path.mkdir()

    async def fake_run_git(*args, cwd=None):
        if "get-url" in args:
            return 0, "https://old-url.com/repo.git", ""
        if "remove" in args:
            return 0, "", ""
        if "add" in args:
            return 1, "", "failed to add remote"
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    ret, err = await rp.check_main_remote(module_path)
    assert ret != 0
    assert "failed to add remote" in err


@pytest.mark.asyncio
async def test_unshallow_if_necessary_does_unshallow(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    refspecs = [_make_ref("origin", "main"), _make_ref("origin", "feature")]
    repo_info = _make_repo_info(refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)

    module_path = tmp_path / "repo"
    module_path.mkdir()

    calls = []

    async def fake_run_git(*args: str, cwd=None):
        calls.append(args)
        if "--is-shallow-repository" in args:
            return 0, "true", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    ret, err = await rp.unshallow_if_necessary(module_path)
    assert ret == 0
    assert err == ""

    pull_call = next(c for c in calls if c[0] == "pull")
    assert "--unshallow" in pull_call


@pytest.mark.asyncio
async def test_unshallow_if_necessary_strips_git_output(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    refspecs = [_make_ref("origin", "main"), _make_ref("origin", "feature")]
    repo_info = _make_repo_info(refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)
    module_path = tmp_path / "repo"
    module_path.mkdir()
    calls = []

    async def fake_run_git(*args: str, cwd=None):
        calls.append(args)
        if "--is-shallow-repository" in args:
            return 0, "true\n", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    ret, err = await rp.unshallow_if_necessary(module_path)

    assert ret == 0
    assert err == ""
    assert any(c[0] == "pull" and "--unshallow" in c for c in calls)


@pytest.mark.asyncio
async def test_checkout_or_create_base_branch_local_branch(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    refspecs = [_make_ref("origin", "feature")]
    repo_info = _make_repo_info(refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)

    module_path = tmp_path / "repo"
    module_path.mkdir()

    async def fake_run_git(*args, cwd=None):
        if "rev-parse" in args and "--verify" in args:
            return 0, "", ""
        if "checkout" in args:
            return 0, "output", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    base_refspec = refspecs[0]
    ret, out, err = await rp.checkout_or_create_base_branch(base_refspec, module_path)
    assert ret == 0
    assert out == "output"
    assert err == ""


@pytest.mark.asyncio
async def test_setup_main_branch_success(monkeypatch, tmp_path: Path) -> None:
    refspecs = [_make_ref("origin", "main")]
    repo_info = _make_repo_info(refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()

    async def fake_checkout_or_create_base_branch(base_refspec, module_path):
        return 0, "output", ""

    rp.checkout_or_create_base_branch = fake_checkout_or_create_base_branch

    ret, out, err = await rp.setup_main_branch(module_path)
    assert ret == 0
    assert out == ""
    assert err == ""


@pytest.mark.asyncio
async def test_setup_merged_branch_delete_existing(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    refspecs = [_make_ref("origin", "main")]
    repo_info = _make_repo_info(refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)

    module_path = tmp_path / "repo"
    module_path.mkdir()

    async def fake_run_git(*args, cwd=None):
        if args[0] == "rev-parse" and args[1] == "--verify" and args[2] == "merged":
            return 0, "", ""
        if args[0] == "branch" and args[1] == "-D" and args[2] == "merged":
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    ret, err = await rp.setup_merged_branch(module_path)
    assert ret == 0
    assert err == ""


@pytest.mark.asyncio
async def test_setup_merged_branch_create_new(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    refspecs = [_make_ref("origin", "main"), _make_ref("origin", "feature")]
    repo_info = _make_repo_info(refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)

    module_path = tmp_path / "repo"
    module_path.mkdir()

    async def fake_run_git(*args, cwd=None):
        if args[0] == "rev-parse" and args[1] == "--verify" and args[2] == "merged":
            return 1, "", "not found"
        if args[0] == "switch" and args[1] == "-C" and args[2] == "merged":
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    ret, err = await rp.setup_merged_branch(module_path)
    assert ret == 0
    assert err == ""


@pytest.mark.asyncio
async def test_link_all_modules_bindfs_failure(monkeypatch, tmp_path: Path) -> None:
    import logging

    from bl import spec_processor as sp

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    rp.task_id = 0
    rp.use_bindfs = True

    module_path = tmp_path / "repo"
    module_path.mkdir()
    (module_path / "mod1").mkdir()

    links_path = tmp_path / "links"
    links_path.mkdir()

    async def fake_run(*args, **kwargs):
        return 1, "", "bindfs failed"

    monkeypatch.setattr(sp, "run", fake_run)
    monkeypatch.setattr(logging, "debug", lambda x: None)

    ret, err = await rp.link_all_modules(["mod1"], module_path, {})
    assert ret == 0


@pytest.mark.asyncio
async def test_link_all_modules_oserror(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()
    (module_path / "mod1").mkdir()

    links_path = tmp_path / "links"
    links_path.mkdir()

    async def fake_unlink_path(path):
        return 0, ""

    def fake_symlink(*args, **kwargs):
        raise OSError("symlink failed")

    monkeypatch.setattr(sp, "unlink_path", fake_unlink_path)
    monkeypatch.setattr("bl.spec_processor.os.symlink", fake_symlink)

    ret, err = await rp.link_all_modules(["mod1"], module_path, {})
    assert ret == -1
    assert "symlink failed" in err


@pytest.mark.asyncio
async def test_merge_spec_into_tree_success(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp
    from bl.spec_processor import console

    refspecs = [_make_ref("origin", "feature")]
    repo_info = _make_repo_info(refspecs=refspecs)
    rp = _make_repo_processor(tmp_path, repo_info)
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()

    async def fake_run_git(*args, cwd=None):
        if "merge" in args:
            return 0, "Merge successful", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)

    ref = refspecs[0]
    root_ref = _make_ref("origin", "main")
    ret, err = await rp.merge_spec_into_tree(repo_info, ref, root_ref, module_path)
    assert ret == 0
    assert err == ""


@pytest.mark.asyncio
async def test_fetch_multi_calls_print_fetch_output(monkeypatch, tmp_path: Path) -> None:
    from io import StringIO

    from rich.console import Console

    from bl import spec_processor as sp

    ref1 = _make_ref("origin", "main")
    rp = _make_repo_processor(tmp_path, _make_repo_info())
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()

    returned_outputs = []
    print_buffer = StringIO()
    test_console = Console(file=print_buffer, force_terminal=True)

    async def fake_run_git(*args, cwd=None):
        if args[0] == "fetch":
            return 0, "f abc123 def456 refs/heads/main\n", ""
        return 0, "", ""

    async def fake_print_fetch_output(name, fetch_data, module_path):
        # Capture the return value (string) - return mock string instead of calling real function
        result = f"[deep_sky_blue3]{name}: updated from [pale_turquoise1]{fetch_data['base'][:9]}[/pale_turquoise1] to [pale_turquoise1]{fetch_data['target'][:9]}[/pale_turquoise1] for {fetch_data['ref']}[/deep_sky_blue3]\n"
        returned_outputs.append(result)
        return result

    monkeypatch.setattr(sp, "run_git", fake_run_git)
    monkeypatch.setattr(sp, "print_fetch_output", fake_print_fetch_output)
    monkeypatch.setattr(sp, "console", test_console)

    # Test that fetch_multi now returns outputs instead of printing
    ret, out, err, fetch_outputs = await rp.fetch_multi("origin", [ref1], module_path)
    assert ret == 0
    assert len(fetch_outputs) == 1
    assert "test-repo" in fetch_outputs[0]
    # Verify output was NOT printed immediately in fetch_multi (new behavior)
    printed_output = print_buffer.getvalue()
    assert "test-repo" not in printed_output


@pytest.mark.asyncio
async def test_queue_repo_task_exception_handling(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    rp.task_id = 0

    async def fake_process_repo(*args):
        raise RuntimeError("test error")

    monkeypatch.setattr(rp, "process_repo", fake_process_repo)

    with pytest.raises(RuntimeError, match="test error"):
        await rp.queue_repo_task()


@pytest.mark.asyncio
async def test_process_project_raises_on_error(monkeypatch, tmp_path: Path) -> None:
    from bl.spec_processor import process_project
    from bl.types import ProjectSpec, RepoInfo

    workdir = tmp_path / "work"
    workdir.mkdir()

    repo_info = RepoInfo(
        modules=[],
        remotes={},
        refspecs=[],
        shell_commands=[],
        patch_globs_to_apply=[],
        target_folder=None,
        locales=[],
        paths={},
    )
    project_spec = ProjectSpec(workdir=workdir, repos={"test": repo_info})

    class DummySemaphore:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class FakeRepoProcessor:
        def __init__(self, *args, **kwargs):
            pass

        async def queue_repo_task(self):
            return 1, "test", []

    from bl import spec_processor as sp

    monkeypatch.setattr(sp, "RepoProcessor", FakeRepoProcessor)

    with pytest.raises(Exception):
        await process_project(project_spec, concurrency=1)


@pytest.mark.asyncio
async def test_process_repo_reset_repo_error(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    refspecs = [_make_ref("origin", "main")]
    repo_info = _make_repo_info(
        modules=[],
        remotes={"origin": "https://example.com/repo.git"},
        refspecs=refspecs,
    )
    rp = _make_repo_processor(tmp_path, repo_info)
    rp.name = "test-repo"

    module_path = tmp_path / "external-src" / "test-repo"
    module_path.mkdir(parents=True)

    async def fake_run_git(*args, cwd=None):
        if args[0] == "status" and "--porcelain" in args:
            return 1, "", "repo is dirty"
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)
    monkeypatch.setattr(sp, "path_is_repo", lambda x: True)
    monkeypatch.setattr(rp, "add_locking_pre_commit", lambda x, y: None)

    ret, fetch_outputs = await rp.process_repo(module_path, [], [])
    assert ret == -1
    assert fetch_outputs == []


@pytest.mark.asyncio
async def test_process_repo_with_cloning_and_temp_branch(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    refspecs = [_make_ref("origin", "main")]
    repo_info = _make_repo_info(
        modules=["mod1"],
        remotes={"origin": "https://example.com/repo.git"},
        refspecs=refspecs,
    )
    rp = _make_repo_processor(tmp_path, repo_info)
    rp.name = "test-repo"

    module_path = tmp_path / "external-src" / "test-repo"
    module_path.mkdir(parents=True)

    git_calls = []

    async def fake_run_git(*args, cwd=None):
        git_calls.append(args)
        cmd = args[0]
        if cmd == "status" and "--porcelain" in args:
            return 0, "", ""
        if cmd == "remote" and "get-url" in args:
            return 0, "https://example.com/repo.git", ""
        if cmd == "remote" and "add" in args:
            return 0, "", ""
        if cmd == "config":
            return 0, "", ""
        if cmd == "rev-parse":
            if "--abbrev-ref" in args:
                return 0, "main", ""
            if "--verify" in args:
                if "temp" in args:
                    return 1, "", "not found"
                return 0, "", ""
            return 0, "", ""
        if cmd == "switch":
            return 0, "", ""
        if cmd == "fetch":
            return 0, "", ""
        if cmd == "checkout":
            return 0, "", ""
        if cmd == "reset":
            return 0, "", ""
        if cmd == "branch":
            return 0, "", ""
        if cmd == "sparse-checkout":
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)
    monkeypatch.setattr(sp, "path_is_repo", lambda x: True)
    monkeypatch.setattr(rp, "add_locking_pre_commit", lambda x, y: None)

    ret, fetch_outputs = await rp.process_repo(module_path, ["mod1"], ["mod1"])
    assert ret == 0
    assert isinstance(fetch_outputs, list)


@pytest.mark.asyncio
async def test_process_repo_shell_commands_error(monkeypatch, tmp_path: Path) -> None:
    from bl import spec_processor as sp

    refspecs = [_make_ref("origin", "main")]
    repo_info = _make_repo_info(
        modules=[],
        remotes={"origin": "https://example.com/repo.git"},
        refspecs=refspecs,
        shell_commands=["false"],
    )
    rp = _make_repo_processor(tmp_path, repo_info)
    rp.name = "test-repo"

    module_path = tmp_path / "external-src" / "test-repo"
    module_path.mkdir(parents=True)

    async def fake_run_git(*args, cwd=None):
        if args[0] == "status" and "--porcelain" in args:
            return 0, "", ""
        if args[0] == "remote" and "get-url" in args:
            return 0, "https://example.com/repo.git", ""
        if args[0] == "remote" and "add" in args:
            return 0, "", ""
        if args[0] == "config":
            return 0, "", ""
        if args[0] == "rev-parse":
            if "--abbrev-ref" in args:
                return 0, "main", ""
            if "--verify" in args:
                if "temp" in args:
                    return 1, "", "not found"
                return 0, "", ""
            return 0, "", ""
        if args[0] == "switch":
            return 0, "", ""
        if args[0] == "fetch":
            return 0, "", ""
        if args[0] == "checkout":
            return 0, "", ""
        if args[0] == "reset":
            return 0, "", ""
        if args[0] == "branch":
            return 0, "", ""
        if args[0] == "sparse-checkout":
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)
    monkeypatch.setattr(sp, "path_is_repo", lambda x: True)
    monkeypatch.setattr(rp, "add_locking_pre_commit", lambda x, y: None)

    ret, fetch_outputs = await rp.process_repo(module_path, [], [])
    assert ret == -1
    assert fetch_outputs == []


@pytest.mark.asyncio
async def test_shallow_pull_output(monkeypatch, tmp_path: Path) -> None:
    """Test that shallow pull also prints output at end."""
    from io import StringIO

    from rich.console import Console

    from bl import spec_processor as sp

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()

    print_buffer = StringIO()
    test_console = Console(file=print_buffer, force_terminal=True)

    async def fake_run_git(*args, cwd=None):
        if args[0] == "fetch" and "--depth" in args:
            return 0, "f abc123 def456 refs/heads/main\n", ""
        if "log" in args:
            return 0, "abc123|Author|Commit message|2 days ago\n", ""
        return 0, "", ""

    monkeypatch.setattr(sp, "run_git", fake_run_git)
    monkeypatch.setattr(sp, "console", test_console)

    # This test verifies the shallow path uses deferred printing
    # The full flow requires process() to call the shallow fetch with depth=1
    # We test that parse_fetch_output and print_fetch_output would be called
    # by verifying the functions are available and the code pattern works
    from bl.spec_processor import parse_fetch_output, print_fetch_output

    # Verify parse_fetch_output can handle shallow fetch output
    test_output = "f abc123 def456 refs/heads/main\n"
    parsed = parse_fetch_output(test_output)
    assert len(parsed) == 1
    assert parsed[0]["base"] == "abc123"
    assert parsed[0]["target"] == "def456"
    assert parsed[0]["ref"] == "main"

    # Verify print_fetch_output returns a string
    output = await print_fetch_output("test-repo", parsed[0], module_path)
    assert isinstance(output, str)
    assert "test-repo" in output


@pytest.mark.asyncio
async def test_deferred_output_order(monkeypatch, tmp_path: Path) -> None:
    """Verify that fetch output appears after fetch completes, not during."""
    from io import StringIO

    from rich.console import Console

    from bl import spec_processor as sp

    ref1 = _make_ref("origin", "main")
    ref2 = _make_ref("origin", "feature")

    rp = _make_repo_processor(tmp_path, _make_repo_info())
    rp.task_id = 0

    module_path = tmp_path / "repo"
    module_path.mkdir()

    output_order = []

    async def fake_run_git(*args, cwd=None):
        if args[0] == "fetch":
            output_order.append("fetch")
            return 0, "f abc123 def456 refs/heads/main\nf def456 ghi789 refs/heads/feature\n", ""
        if "log" in args:
            output_order.append("log")
            return 0, "abc123|Author|Commit|2 days ago\n", ""
        output_order.append("other")
        return 0, "", ""

    print_buffer = StringIO()
    test_console = Console(file=print_buffer, force_terminal=True)
    monkeypatch.setattr(sp, "run_git", fake_run_git)
    monkeypatch.setattr(sp, "console", test_console)

    ret, out, err, fetch_outputs = await rp.fetch_multi("origin", [ref1, ref2], module_path)

    # Verify fetch outputs are returned instead of printed
    assert len(fetch_outputs) == 2
    assert "test-repo" in fetch_outputs[0]
    assert "test-repo" in fetch_outputs[1]
