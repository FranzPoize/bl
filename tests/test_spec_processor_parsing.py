from __future__ import annotations

from pathlib import Path

import pytest

from bl.spec_processor import parse_fetch_output, print_fetch_output, rich_warning


def test_rich_warning() -> None:
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
