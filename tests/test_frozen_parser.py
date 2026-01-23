"""Parser-level tests for frozen_sha wiring."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from bl.spec_parser import load_spec_file


def test_frozen_sha_populated_from_mapping() -> None:
    """Test that RefspecInfo.frozen_sha is populated from frozen.yaml mapping."""
    frozen_mapping = {
        "sale-promotion": {
            "oca": {
                "14.0": "a" * 40,
                "refs/pull/188/head": "b" * 40,
            },
            "ak": {
                "14.0-sale_coupon_invoice_delivered": "c" * 40,
            },
        }
    }

    spec_data = {
        "sale-promotion": {
            "modules": [],
            "remotes": {
                "oca": "https://example.com/OCA/sale-promotion",
                "ak": "https://example.com/akretion/sale-promotion",
            },
            "merges": [
                "oca 14.0",
                "oca refs/pull/188/head",
                "ak 14.0-sale_coupon_invoice_delivered",
            ],
        },
    }

    with TemporaryDirectory() as td:
        td_path = Path(td)
        spec_path = td_path / "spec.yaml"
        frozen_path = td_path / "frozen.yaml"

        spec_path.write_text(yaml.safe_dump(spec_data))
        frozen_path.write_text(yaml.safe_dump(frozen_mapping))

        project = load_spec_file(spec_path, frozen_path, td_path)
        assert project is not None, "ProjectSpec should not be None"

        sale = project.specs["sale-promotion"]
        assert sale.frozen_modules == frozen_mapping["sale-promotion"]
        assert sale.refspec_info is not None
        assert [r.refspec for r in sale.refspec_info] == [
            "14.0",
            "refs/pull/188/head",
            "14.0-sale_coupon_invoice_delivered",
        ]
        assert [r.frozen_sha for r in sale.refspec_info] == [
            frozen_mapping["sale-promotion"]["oca"]["14.0"],
            frozen_mapping["sale-promotion"]["oca"]["refs/pull/188/head"],
            frozen_mapping["sale-promotion"]["ak"]["14.0-sale_coupon_invoice_delivered"],
        ]


def test_frozen_modules_none_when_section_missing() -> None:
    """Test that ModuleSpec.frozen_modules is None when section has no entry in frozen.yaml."""
    frozen_mapping = {
        "sale-promotion": {
            "oca": {
                "14.0": "a" * 40,
            },
        }
    }

    spec_data = {
        "sale-promotion": {
            "modules": [],
            "remotes": {
                "oca": "https://example.com/OCA/sale-promotion",
            },
            "merges": ["oca 14.0"],
        },
        # Section with no freezes on purpose
        "queue": {
            "modules": [],
            "remotes": {
                "oca": "https://example.com/OCA/queue",
            },
            "merges": ["oca 14.0"],
        },
    }

    with TemporaryDirectory() as td:
        td_path = Path(td)
        spec_path = td_path / "spec.yaml"
        frozen_path = td_path / "frozen.yaml"

        spec_path.write_text(yaml.safe_dump(spec_data))
        frozen_path.write_text(yaml.safe_dump(frozen_mapping))

        project = load_spec_file(spec_path, frozen_path, td_path)
        assert project is not None

        queue = project.specs["queue"]
        assert queue.frozen_modules is None
        assert queue.refspec_info is not None
        assert len(queue.refspec_info) == 1
        assert queue.refspec_info[0].frozen_sha is None


def test_frozen_sha_none_when_refspec_missing() -> None:
    """Test that frozen_sha is None when a refspec has no entry in frozen.yaml."""
    frozen_mapping = {
        "sale-promotion": {
            "oca": {
                "14.0": "a" * 40,
                # Missing entry for refs/pull/188/head
            },
        }
    }

    spec_data = {
        "sale-promotion": {
            "modules": [],
            "remotes": {
                "oca": "https://example.com/OCA/sale-promotion",
            },
            "merges": [
                "oca 14.0",
                "oca refs/pull/188/head",  # This one has no freeze
            ],
        },
    }

    with TemporaryDirectory() as td:
        td_path = Path(td)
        spec_path = td_path / "spec.yaml"
        frozen_path = td_path / "frozen.yaml"

        spec_path.write_text(yaml.safe_dump(spec_data))
        frozen_path.write_text(yaml.safe_dump(frozen_mapping))

        project = load_spec_file(spec_path, frozen_path, td_path)
        assert project is not None

        sale = project.specs["sale-promotion"]
        assert sale.refspec_info is not None
        assert len(sale.refspec_info) == 2
        assert sale.refspec_info[0].frozen_sha == "a" * 40  # Has freeze
        assert sale.refspec_info[1].frozen_sha is None  # Missing freeze
