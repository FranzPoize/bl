from __future__ import annotations

from pathlib import Path

from bl.types import CloneFlags, OriginType, ProjectSpec, RefspecInfo, RepoInfo


class TestRefspecInfoRepr:
    def test_refspec_info_repr_without_ref_name(self) -> None:
        ref = RefspecInfo("origin", "main", OriginType.BRANCH, None)
        result = repr(ref)
        assert "origin" in result
        assert "main" in result
        assert "branch" in result

    def test_refspec_info_repr_with_ref_name(self) -> None:
        ref = RefspecInfo("origin", "abc123", OriginType.REF, "my-branch")
        result = repr(ref)
        assert "origin" in result
        assert "abc123" in result
        assert "ref" in result


class TestRepoInfoRepr:
    def test_repo_info_repr(self) -> None:
        ref = RefspecInfo("origin", "main", OriginType.BRANCH, None)
        repo = RepoInfo(
            modules=["mod1"],
            remotes={"origin": "https://example.com"},
            refspecs=[ref],
            paths={"/path": ["mod1"]},
        )
        result = repr(repo)
        assert "mod1" in result
        assert "origin" in result
        assert "/path" in result


class TestProjectSpecRepr:
    def test_project_spec_repr(self) -> None:
        ref = RefspecInfo("origin", "main", OriginType.BRANCH, None)
        repo = RepoInfo(
            modules=["mod1"],
            remotes={"origin": "https://example.com"},
            refspecs=[ref],
        )
        project = ProjectSpec(repos={"my-repo": repo}, workdir=Path("/tmp"))
        result = repr(project)
        assert "my-repo" in result
        assert "/tmp" in result


class TestCloneFlags:
    def test_clone_flags_values(self) -> None:
        assert CloneFlags.SHALLOW == 1
        assert CloneFlags.SPARSE == 2

    def test_clone_flags_combined(self) -> None:
        flags = CloneFlags.SHALLOW | CloneFlags.SPARSE
        assert flags == 3
        assert flags & CloneFlags.SHALLOW
        assert flags & CloneFlags.SPARSE


class TestOriginType:
    def test_origin_type_values(self) -> None:
        assert OriginType.BRANCH.value == "branch"
        assert OriginType.PR.value == "pr"
        assert OriginType.REF.value == "ref"
