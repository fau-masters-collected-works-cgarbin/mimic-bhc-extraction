"""Tests for compute_bhc_stats."""

import pytest

from mimic_bhc.model import BHCResult
from mimic_bhc.stats import compute_bhc_stats
from mimic_bhc.structure import StructureResult


def _make_bhc_result(
    *,
    success: bool = True,
    bhc_text: str | None = "Short text.",
    start_header: str | None = "Brief Hospital Course:",
    end_header: str | None = "Medications on Discharge",
    failure_reason: str | None = None,
    category: str = "narrative",
) -> BHCResult:
    """Build a BHCResult for stats tests."""
    structure = StructureResult(category=category) if success else None
    return BHCResult(
        extraction_success=success,
        bhc_text=bhc_text if success else None,
        start_header=start_header if success else None,
        end_header=end_header if success else None,
        failure_reason=failure_reason if not success else None,
        bhc_structure=structure,
    )


class TestComputeBhcStats:
    def test_empty_list(self):
        stats = compute_bhc_stats([])
        assert stats["total"] == 0
        assert stats["successes"] == 0
        assert stats["success_rate"] == 0.0
        assert stats["failure_reasons"] == {}
        assert stats["bhc_length_stats"] == {}
        assert stats["short_extractions"] == 0
        assert stats["format_distribution"] == {}

    def test_stats_on_known_input(self):
        results = [
            _make_bhc_result(
                bhc_text="Short text.",
                start_header="Brief Hospital Course:",
                end_header="Medications on Discharge",
                category="narrative",
            ),
            _make_bhc_result(
                bhc_text="A longer piece of text here.",
                start_header="Hospital Course:",
                end_header="Discharge Condition",
                category="domain_organized",
            ),
            _make_bhc_result(
                success=False,
                failure_reason="no_header",
            ),
        ]

        stats = compute_bhc_stats(results)
        assert stats["total"] == 3
        assert stats["successes"] == 2
        assert stats["failures"] == 1
        assert stats["success_rate"] == pytest.approx(2 / 3)
        assert stats["failure_reasons"]["no_header"] == 1
        assert "Brief Hospital Course:" in stats["header_distribution"]
        assert "Hospital Course:" in stats["header_distribution"]
        assert stats["bhc_length_stats"]["min"] == len("Short text.")
        assert stats["bhc_length_stats"]["max"] == len("A longer piece of text here.")
        assert stats["short_extractions"] == 2
        assert stats["format_distribution"] == {
            "narrative": 1,
            "domain_organized": 1,
        }

    def test_short_extractions_threshold(self):
        results = [
            _make_bhc_result(bhc_text="x" * 50),
            _make_bhc_result(bhc_text="x" * 100),
            _make_bhc_result(bhc_text="x" * 200),
        ]

        stats = compute_bhc_stats(results)
        assert stats["short_extractions"] == 1
