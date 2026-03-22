"""Tests for mimic_bhc.audit (audit entry generation)."""

from mimic_bhc.audit import (
    RARE_THRESHOLD,
    build_audit_entries,
)
from mimic_bhc.model import BHCResult
from mimic_bhc.stats import MIN_BHC_LENGTH


def _make_result(
    hadm_id: int = 1,
    bhc_text: str = "A" * 200,
    start_header: str = "Brief Hospital Course:",
    end_header: str = "Discharge Medications:",
    *,
    success: bool = True,
):
    """Create a BHCResult for testing."""
    if success:
        return BHCResult(
            extraction_success=True,
            bhc_text=bhc_text,
            start_header=start_header,
            end_header=end_header,
            failure_reason=None,
            hadm_id=hadm_id,
        )
    return BHCResult(
        extraction_success=False,
        bhc_text=None,
        start_header=None,
        end_header=None,
        failure_reason="no_header",
        hadm_id=hadm_id,
    )


class TestBuildAuditEntries:
    def test_basic_entry_structure(self):
        """Each entry should have required fields."""
        results = [_make_result()]
        entries = build_audit_entries(results)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["hadm_id"] == 1
        assert entry["success"] is True
        assert "review_flags" in entry
        assert "review_reasons" in entry
        assert "extracted_at" in entry

    def test_failed_extraction_flagged(self):
        """Failed extractions should be flagged with their failure reason."""
        results = [_make_result(success=False)]
        entries = build_audit_entries(results)
        assert "no_header" in entries[0]["review_flags"]
        assert "no_header" in entries[0]["review_reasons"]

    def test_short_extraction_flagged(self):
        """Extractions below MIN_BHC_LENGTH should get a 'short' flag."""
        results = [_make_result(bhc_text="Short")]
        assert len("Short") < MIN_BHC_LENGTH
        entries = build_audit_entries(results)
        assert "short" in entries[0]["review_flags"]

    def test_unbounded_extraction_flagged(self):
        """Extractions with no end header should get an 'unbounded' flag."""
        results = [_make_result(end_header=None)]
        entries = build_audit_entries(results)
        assert "unbounded" in entries[0]["review_flags"]

    def test_rare_header_flagged(self):
        """Headers appearing fewer than RARE_THRESHOLD times should be flagged."""
        common = [_make_result(hadm_id=i) for i in range(RARE_THRESHOLD + 1)]
        rare = _make_result(hadm_id=999, start_header="Rare Header:")
        results = [*common, rare]
        entries = build_audit_entries(results)
        rare_entry = next(e for e in entries if e["hadm_id"] == 999)
        assert "rare_header" in rare_entry["review_flags"]

    def test_clean_entries_not_flagged(self):
        """Normal successful extractions should have no flags (except possible spot-check)."""
        results = [_make_result(hadm_id=i) for i in range(100)]
        entries = build_audit_entries(results)
        non_spot = [e for e in entries if "sampled_for_spot_check" not in e["review_flags"]]
        for entry in non_spot:
            assert entry["review_flags"] == []

    def test_spot_check_deterministic(self):
        """Spot-check selection should be deterministic with the same seed."""
        results = [_make_result(hadm_id=i) for i in range(200)]
        entries1 = build_audit_entries(results, spot_check_seed=42)
        entries2 = build_audit_entries(results, spot_check_seed=42)
        spotted1 = [e["hadm_id"] for e in entries1 if "sampled_for_spot_check" in e["review_flags"]]
        spotted2 = [e["hadm_id"] for e in entries2 if "sampled_for_spot_check" in e["review_flags"]]
        assert spotted1 == spotted2
        assert len(spotted1) > 0
