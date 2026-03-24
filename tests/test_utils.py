from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from bl.types import OriginType, RefspecInfo, RepoInfo
from bl.utils import format_diff, get_local_ref, get_module_path, unlink_path


class TestFormatDiff:
    def test_format_diff_short_message(self) -> None:
        result = format_diff("short message")
        assert result == "short message"

    def test_format_diff_long_message_truncated(self) -> None:
        lines = ["line1", "line2", "line3", "line4", "line5", "line6", "line7"]
        msg = "\n".join(lines)
        result = format_diff(msg)
        expected = "line1\nline2\nline3\nline4\nline5\n..."
        assert result == expected


class TestGetLocalRef:
    def test_get_local_ref_with_ref_name(self) -> None:
        ref = RefspecInfo("origin", "abc123", OriginType.REF, "my-branch")
        result = get_local_ref(ref)
        assert result == "my-branch"

    def test_get_local_ref_without_ref_name(self) -> None:
        ref = RefspecInfo("origin", "main", OriginType.BRANCH, None)
        result = get_local_ref(ref)
        assert result == "main"


class TestGetModulePath:
    def test_get_module_path_odoo_without_target_folder_deprecated(self, tmp_path: Path) -> None:
        module_spec = RepoInfo(modules=[], remotes={}, target_folder=None)
        with pytest.warns(DeprecationWarning):
            result = get_module_path(tmp_path, "odoo", module_spec)
        assert result == tmp_path / "src/"

    def test_get_module_path_odoo_with_target_folder(self, tmp_path: Path) -> None:
        module_spec = RepoInfo(modules=[], remotes={}, target_folder="custom/")
        result = get_module_path(tmp_path, "odoo", module_spec)
        assert result == tmp_path / "custom/"

    def test_get_module_path_non_odoo_with_target_folder(self, tmp_path: Path) -> None:
        module_spec = RepoInfo(modules=[], remotes={}, target_folder="addons/")
        result = get_module_path(tmp_path, "addons", module_spec)
        assert result == tmp_path / "addons/"

    def test_get_module_path_non_odoo_default(self, tmp_path: Path) -> None:
        module_spec = RepoInfo(modules=[], remotes={}, target_folder=None)
        result = get_module_path(tmp_path, "my-addon", module_spec)
        assert result == tmp_path / "external-src" / "my-addon"


class TestUnlinkPath:
    @pytest.mark.asyncio
    async def test_unlink_path_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target, target_is_directory=True)
        ret, err = await unlink_path(link)
        assert ret == 0
        assert not link.exists()

    @pytest.mark.asyncio
    async def test_unlink_path_mount_fail(self, tmp_path: Path) -> None:
        mount_point = tmp_path / "mount"
        mount_point.mkdir()
        with patch("pathlib.Path.is_mount", return_value=True):
            with patch("bl.utils.run", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = (1, "", "umount failed")
                ret, err = await unlink_path(mount_point)
                assert ret == -1
                assert "Failed to unmount" in err

    @pytest.mark.asyncio
    async def test_unlink_path_directory(self, tmp_path: Path) -> None:
        dir_path = tmp_path / "empty_dir"
        dir_path.mkdir()
        ret, err = await unlink_path(dir_path)
        assert ret == 0
        assert not dir_path.exists()

    @pytest.mark.asyncio
    async def test_unlink_path_oserror(self, tmp_path: Path) -> None:
        path = tmp_path / "path"
        with patch("pathlib.Path.is_symlink", return_value=False):
            with patch("pathlib.Path.is_mount", return_value=False):
                with patch("pathlib.Path.exists", return_value=True):
                    with patch("pathlib.Path.is_dir", return_value=True):
                        with patch("bl.utils.asyncio.to_thread", side_effect=OSError("test")):
                            ret, err = await unlink_path(path)
                            assert ret == -1
                            assert "test" in err
