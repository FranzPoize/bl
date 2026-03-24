from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bl.spec_parser import (
    get_origin_type,
    get_with_syntax_check,
    load_spec_file,
    make_remote_merge_from_src,
    merge_configs,
)


class TestMergeConfigs:
    def test_merge_configs_basic_override(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        merge_configs(base, override)
        assert base == {"a": 1, "b": 3, "c": 4}

    def test_merge_configs_nested_dict(self) -> None:
        base = {"outer": {"inner1": 1, "inner2": 2}}
        override = {"outer": {"inner2": 3, "inner3": 4}}
        merge_configs(base, override)
        assert base == {"outer": {"inner1": 1, "inner2": 3, "inner3": 4}}

    def test_merge_configs_list_replace(self) -> None:
        base = {"modules": ["a", "b"]}
        override = {"modules": ["c", "d"]}
        merge_configs(base, override)
        assert base == {"modules": ["c", "d"]}

    def test_merge_configs_list_extend_with_ellipsis(self) -> None:
        base = {"modules": ["a", "b"]}
        override = {"modules": ["start", "...", "end"]}
        merge_configs(base, override)
        assert base == {"modules": ["start", "a", "b", "end"]}

    def test_merge_configs_list_ellipsis_at_start(self) -> None:
        base = {"modules": ["a", "b"]}
        override = {"modules": ["...", "c"]}
        merge_configs(base, override)
        assert base == {"modules": ["a", "b", "c"]}

    def test_merge_configs_list_ellipsis_at_end(self) -> None:
        base = {"modules": ["a", "b"]}
        override = {"modules": ["c", "..."]}
        merge_configs(base, override)
        assert base == {"modules": ["c", "a", "b"]}

    def test_merge_configs_add_new_key(self) -> None:
        base = {"a": 1}
        override = {"b": 2}
        merge_configs(base, override)
        assert base == {"a": 1, "b": 2}

    def test_merge_configs_deep_nesting(self) -> None:
        base = {"level1": {"level2": {"level3": 1}}}
        override = {"level1": {"level2": {"level3": 2, "level4": 3}}}
        merge_configs(base, override)
        assert base == {"level1": {"level2": {"level3": 2, "level4": 3}}}

    def test_merge_configs_mixed_types_replace(self) -> None:
        base = {"value": 1}
        override = {"value": "string"}
        merge_configs(base, override)
        assert base == {"value": "string"}

    def test_merge_configs_empty_override(self) -> None:
        base = {"a": 1, "b": 2}
        override = {}
        merge_configs(base, override)
        assert base == {"a": 1, "b": 2}

    def test_merge_configs_empty_base(self) -> None:
        base = {}
        override = {"a": 1, "b": 2}
        merge_configs(base, override)
        assert base == {"a": 1, "b": 2}


class TestLoadSpecFileWithOverride:
    def test_load_spec_file_with_override(self, tmp_path: Path) -> None:
        base_config = tmp_path / "spec.yaml"
        override_config = tmp_path / "override.yaml"

        base_config.write_text(
            yaml.safe_dump(
                {
                    "test-repo": {
                        "modules": ["mod1", "mod2"],
                        "remotes": {"origin": "https://example.com/repo.git"},
                        "merges": ["origin main"],
                    }
                }
            )
        )

        override_config.write_text(
            yaml.safe_dump(
                {
                    "test-repo": {
                        "modules": ["mod3", "..."],
                    }
                }
            )
        )

        project = load_spec_file(base_config, None, tmp_path, override_config)
        assert project is not None
        repo_info = project.repos["test-repo"]
        assert "mod3" in repo_info.modules

    def test_load_spec_file_without_override(self, tmp_path: Path) -> None:
        config = tmp_path / "spec.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "test-repo": {
                        "modules": ["mod1"],
                        "remotes": {"origin": "https://example.com/repo.git"},
                        "merges": ["origin main"],
                    }
                }
            )
        )

        project = load_spec_file(config, None, tmp_path)
        assert project is not None
        repo_info = project.repos["test-repo"]
        assert repo_info.modules == ["mod1"]

    def test_load_spec_file_override_adds_repo(self, tmp_path: Path) -> None:
        base_config = tmp_path / "spec.yaml"
        override_config = tmp_path / "override.yaml"

        base_config.write_text(
            yaml.safe_dump(
                {
                    "existing-repo": {
                        "modules": [],
                        "remotes": {"origin": "https://example.com/repo.git"},
                        "merges": ["origin main"],
                    }
                }
            )
        )

        override_config.write_text(
            yaml.safe_dump(
                {
                    "new-repo": {
                        "modules": ["new_mod"],
                        "remotes": {"origin": "https://example.com/new.git"},
                        "merges": ["origin main"],
                    }
                }
            )
        )

        project = load_spec_file(base_config, None, tmp_path, override_config)
        assert project is not None
        assert "existing-repo" in project.repos
        assert "new-repo" in project.repos
        assert project.repos["new-repo"].modules == ["new_mod"]


class TestPathsFieldExtraction:
    def test_load_spec_file_extracts_paths(self, tmp_path: Path) -> None:
        config = tmp_path / "spec.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "test-repo": {
                        "modules": ["mod1", "mod2"],
                        "remotes": {"origin": "https://example.com/repo.git"},
                        "merges": ["origin main"],
                        "paths": {"/custom/path": ["mod1"], "/another/path": []},
                    }
                }
            )
        )

        project = load_spec_file(config, None, tmp_path)
        assert project is not None
        repo_info = project.repos["test-repo"]
        assert repo_info.paths == {"/custom/path": ["mod1"], "/another/path": []}

    def test_load_spec_file_paths_default_empty(self, tmp_path: Path) -> None:
        config = tmp_path / "spec.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "test-repo": {
                        "modules": ["mod1"],
                        "remotes": {"origin": "https://example.com/repo.git"},
                        "merges": ["origin main"],
                    }
                }
            )
        )

        project = load_spec_file(config, None, tmp_path)
        assert project is not None
        repo_info = project.repos["test-repo"]
        assert repo_info.paths == {}


class TestGetOriginType:
    def test_get_origin_type_branch(self) -> None:
        from bl.types import OriginType

        assert get_origin_type("main") == OriginType.BRANCH
        assert get_origin_type("develop") == OriginType.BRANCH
        assert get_origin_type("feature/my-feature") == OriginType.BRANCH

    def test_get_origin_type_pr_ref(self) -> None:
        from bl.types import OriginType

        assert get_origin_type("refs/pull/123/head") == OriginType.PR

    def test_get_origin_type_ref_hash(self) -> None:
        from bl.types import OriginType

        assert get_origin_type("a" * 40) == OriginType.REF
        assert get_origin_type("0" * 40) == OriginType.REF
        assert get_origin_type("abcdef1234567890abcdef1234567890abcdef12") == OriginType.REF


class TestGetWithSyntaxCheck:
    def test_get_with_syntax_check_valid(self) -> None:
        data = {"key": "value"}
        result = get_with_syntax_check("test", data, "key", str)
        assert result == "value"

    def test_get_with_syntax_check_default(self) -> None:
        data = {}
        result = get_with_syntax_check("test", data, "key", str)
        assert result == ""

    def test_get_with_syntax_check_wrong_type_raises(self) -> None:
        data = {"key": 123}
        with pytest.raises(Exception) as exc_info:
            get_with_syntax_check("test", data, "key", str)
        assert "not of proper syntax" in str(exc_info.value)


class TestLoadSpecFileErrors:
    def test_load_spec_file_config_not_found(self, tmp_path: Path) -> None:
        config = tmp_path / "nonexistent.yaml"
        project = load_spec_file(config, None, tmp_path)
        assert project is None

    def test_load_spec_file_config_in_subdirectory(self, tmp_path: Path) -> None:
        config = tmp_path / "spec.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "test-repo": {
                        "modules": ["mod1"],
                        "remotes": {"origin": "https://example.com/repo.git"},
                        "merges": ["origin main"],
                    }
                }
            )
        )
        (tmp_path / "odoo").mkdir()
        project = load_spec_file(config, None, tmp_path)
        assert project is not None
        assert "test-repo" in project.repos

    def test_load_spec_file_yaml_parse_error(self, tmp_path: Path) -> None:
        config = tmp_path / "spec.yaml"
        config.write_text("invalid: yaml: content:")
        project = load_spec_file(config, None, tmp_path)
        assert project is None

    def test_load_spec_file_override_yaml_error(self, tmp_path: Path) -> None:
        config = tmp_path / "spec.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "test-repo": {
                        "modules": ["mod1"],
                        "remotes": {"origin": "https://example.com/repo.git"},
                        "merges": ["origin main"],
                    }
                }
            )
        )
        override = tmp_path / "override.yaml"
        override.write_text("invalid: yaml: content:")
        project = load_spec_file(config, None, tmp_path, override)
        assert project is not None

    def test_load_spec_file_frozen_yaml_error(self, tmp_path: Path) -> None:
        config = tmp_path / "spec.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "test-repo": {
                        "modules": ["mod1"],
                        "remotes": {"origin": "https://example.com/repo.git"},
                        "merges": ["origin main"],
                    }
                }
            )
        )
        frozen = tmp_path / "frozen.yaml"
        frozen.write_text("invalid: yaml: content:")
        project = load_spec_file(config, frozen, tmp_path)
        assert project is not None


def test_make_remote_merge_from_src() -> None:
    remotes, merges = make_remote_merge_from_src("https://example.com/repo.git main")
    assert remotes == {"origin": "https://example.com/repo.git"}
    assert merges == ["origin main"]


def test_load_spec_file_with_src_field(tmp_path: Path) -> None:
    config = tmp_path / "spec.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "test-repo": {
                    "modules": ["mod1"],
                    "src": "https://example.com/repo.git main",
                    "merges": ["origin develop"],
                }
            }
        )
    )
    project = load_spec_file(config, None, tmp_path)
    assert project is not None
    assert "test-repo" in project.repos
    repo = project.repos["test-repo"]
    assert repo.remotes.get("origin") == "https://example.com/repo.git"


def test_load_spec_file_frozen_default_location(tmp_path: Path) -> None:
    config = tmp_path / "spec.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "test-repo": {
                    "modules": ["mod1"],
                    "remotes": {"origin": "https://example.com/repo.git"},
                    "merges": ["origin main"],
                }
            }
        )
    )
    frozen = tmp_path / "frozen.yaml"
    frozen.write_text(yaml.safe_dump({"test-repo": {"origin": {"main": "abc123"}}}))
    project = load_spec_file(config, None, tmp_path)
    assert project is not None


def test_load_spec_file_config_in_odoo_subdir(tmp_path: Path) -> None:
    odoo_dir = tmp_path / "odoo"
    odoo_dir.mkdir()
    config = odoo_dir / "spec.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "test-repo": {
                    "modules": ["mod1"],
                    "remotes": {"origin": "https://example.com/repo.git"},
                    "merges": ["origin main"],
                }
            }
        )
    )
    project = load_spec_file(odoo_dir / "spec.yaml", None, odoo_dir)
    assert project is not None
