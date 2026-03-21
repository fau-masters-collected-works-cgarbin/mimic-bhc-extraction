"""Tests for BHCResult enrichment, properties, and serialization."""

import dataclasses

from mimic_bhc.extract import extract_bhc
from mimic_bhc.model import BHCResult
from mimic_bhc.structure import StructureResult

# Re-use note fixtures from test_extract.
from tests.test_extract import NO_HEADER_NOTE, STANDARD_NOTE


# ---------------------------------------------------------------------------
# BHCResult enrichment, properties, and serialization
# ---------------------------------------------------------------------------
class TestBHCResultEnrichment:
    def test_default_enrichment_fields(self):
        """New enrichment fields should default to None/empty."""
        result = extract_bhc(STANDARD_NOTE, hadm_id=100)
        assert result.subject_id is None
        assert result.chartdate is None
        assert result.note_length is None
        assert result.quality_flags == ()
        assert result.bhc_structure is None

    def test_enrichment_via_replace(self):
        """Callers enrich BHCResult using dataclasses.replace()."""
        result = extract_bhc(STANDARD_NOTE, hadm_id=100)
        structure = StructureResult(category="narrative")
        enriched = dataclasses.replace(
            result,
            subject_id=42,
            chartdate="2150-01-01",
            note_length=len(STANDARD_NOTE),
            quality_flags=("below_min_length",),
            bhc_structure=structure,
        )
        assert enriched.subject_id == 42
        assert enriched.chartdate == "2150-01-01"
        assert enriched.note_length == len(STANDARD_NOTE)
        assert enriched.quality_flags == ("below_min_length",)
        assert enriched.bhc_structure is structure
        # Original extraction fields preserved
        assert enriched.bhc_text == result.bhc_text
        assert enriched.hadm_id == 100

    def test_bhc_char_count_property(self):
        result = extract_bhc(STANDARD_NOTE, hadm_id=100)
        assert result.bhc_char_count == len(result.bhc_text)

    def test_bhc_char_count_none_when_no_text(self):
        result = extract_bhc(NO_HEADER_NOTE, hadm_id=100)
        assert result.bhc_char_count == 0

    def test_bhc_word_count_property(self):
        result = extract_bhc(STANDARD_NOTE, hadm_id=100)
        assert result.bhc_word_count == len(result.bhc_text.split())

    def test_bhc_word_count_none_when_no_text(self):
        result = extract_bhc(NO_HEADER_NOTE, hadm_id=100)
        assert result.bhc_word_count == 0


class TestBHCResultSerialization:
    def test_to_dict_contains_all_fields(self):
        result = extract_bhc(STANDARD_NOTE, hadm_id=100, note_row_id="42")
        structure = StructureResult(category="narrative")
        enriched = dataclasses.replace(
            result,
            subject_id=10,
            chartdate="2150-01-01",
            note_length=500,
            quality_flags=(),
            bhc_structure=structure,
        )
        d = enriched.to_dict()
        assert d["hadm_id"] == 100
        assert d["subject_id"] == 10
        assert d["row_id"] == "42"
        assert d["chartdate"] == "2150-01-01"
        assert d["bhc_text"] == enriched.bhc_text
        assert d["bhc_char_count"] == enriched.bhc_char_count
        assert d["bhc_word_count"] == enriched.bhc_word_count
        assert d["start_header"] == enriched.start_header
        assert d["end_header"] == enriched.end_header
        assert d["extraction_success"] is True
        assert d["failure_reason"] is None
        assert d["quality_flags"] == []
        assert d["bhc_structure"] == {
            "category": "narrative",
            "numbering_convention": None,
            "modifiers": [],
            "nicu_domain_count": 0,
            "labeled_section_count": 0,
        }
        assert d["bhc_start"] == enriched.bhc_start
        assert d["bhc_end"] == enriched.bhc_end
        assert d["note_length"] == 500

    def test_to_dict_failed_extraction(self):
        result = extract_bhc(NO_HEADER_NOTE, hadm_id=100)
        d = result.to_dict()
        assert d["extraction_success"] is False
        assert d["failure_reason"] == "no_header"
        assert d["bhc_text"] is None
        assert d["bhc_structure"] is None
        assert d["bhc_char_count"] == 0
        assert d["bhc_word_count"] == 0

    def test_from_dict_roundtrip(self):
        """from_dict(to_dict(result)) should reconstruct an equivalent BHCResult."""
        result = extract_bhc(STANDARD_NOTE, hadm_id=100, note_row_id="42")
        structure = StructureResult(category="domain_organized", labeled_section_count=3)
        enriched = dataclasses.replace(
            result,
            subject_id=10,
            chartdate="2150-01-01",
            note_length=500,
            quality_flags=("unbounded",),
            bhc_structure=structure,
        )
        d = enriched.to_dict()
        reconstructed = BHCResult.from_dict(d)
        assert reconstructed.hadm_id == enriched.hadm_id
        assert reconstructed.success == enriched.success
        assert reconstructed.bhc_text == enriched.bhc_text
        assert reconstructed.start_header == enriched.start_header
        assert reconstructed.end_header == enriched.end_header
        assert reconstructed.failure_reason == enriched.failure_reason
        assert reconstructed.subject_id == enriched.subject_id
        assert reconstructed.chartdate == enriched.chartdate
        assert reconstructed.note_length == enriched.note_length
        assert reconstructed.quality_flags == enriched.quality_flags
        assert reconstructed.bhc_structure == enriched.bhc_structure
        assert reconstructed.bhc_start == enriched.bhc_start
        assert reconstructed.bhc_end == enriched.bhc_end

    def test_from_dict_failed_extraction(self):
        result = extract_bhc(NO_HEADER_NOTE, hadm_id=100)
        d = result.to_dict()
        reconstructed = BHCResult.from_dict(d)
        assert reconstructed.success is False
        assert reconstructed.bhc_text is None
        assert reconstructed.bhc_structure is None
