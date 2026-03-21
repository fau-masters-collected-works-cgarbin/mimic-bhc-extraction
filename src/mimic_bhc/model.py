"""BHCResult: the canonical data structure for BHC extraction results.

See docs/extraction-design.md for the layered population model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mimic_bhc.structure import StructureResult


@dataclass(frozen=True)
class BHCResult:
    """Result of extracting a BHC section from a single note.

    Fields are populated in layers (see docs/extraction-design.md):
    - Extraction layer (extract_bhc): fields derived directly from
      text extraction (matched text, headers, offsets, success status).
    - Enrichment layer (callers via dataclasses.replace): record
      identifiers, note metadata, quality flags, structure classification.
    """

    # --- Extraction layer ---
    success: bool
    bhc_text: str | None
    start_header: str | None
    end_header: str | None
    failure_reason: str | None  # "no_header" | "empty_section" | None
    bhc_start: int | None = None
    bhc_end: int | None = None

    # --- Enrichment layer (filled by callers) ---
    hadm_id: int | None = None
    note_row_id: str | None = None
    subject_id: int | None = None
    chartdate: str | None = None
    note_length: int | None = None
    note_text: str | None = None
    note_row_count: int | None = None
    quality_flags: tuple[str, ...] = ()
    bhc_structure: StructureResult | None = None

    @property
    def bhc_char_count(self) -> int:
        return len(self.bhc_text) if self.bhc_text else 0

    @property
    def bhc_word_count(self) -> int:
        return len(self.bhc_text.split()) if self.bhc_text else 0

    def to_dict(self) -> dict:
        """Serialize to a dict for JSON output.

        This is the single serialization point. Field names here define
        the JSON schema; no other code should construct output dicts from
        BHCResult fields manually.
        """
        structure_dict = None
        if self.bhc_structure is not None:
            structure_dict = {
                "category": self.bhc_structure.category,
                "numbering_convention": self.bhc_structure.numbering_convention,
                "modifiers": list(self.bhc_structure.modifiers),
                "nicu_domain_count": self.bhc_structure.nicu_domain_count,
                "labeled_section_count": self.bhc_structure.labeled_section_count,
            }

        return {
            "hadm_id": self.hadm_id,
            "subject_id": self.subject_id,
            "row_id": self.note_row_id,
            "chartdate": self.chartdate,
            "bhc_text": self.bhc_text,
            "bhc_char_count": self.bhc_char_count,
            "bhc_word_count": self.bhc_word_count,
            "start_header": self.start_header,
            "end_header": self.end_header,
            "extraction_success": self.success,
            "failure_reason": self.failure_reason,
            "quality_flags": list(self.quality_flags),
            "bhc_structure": structure_dict,
            "bhc_start": self.bhc_start,
            "bhc_end": self.bhc_end,
            "note_length": self.note_length,
            "note_text": self.note_text,
            "note_row_count": self.note_row_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> BHCResult:
        """Reconstruct a BHCResult from a dict (e.g. loaded from JSON).

        Reverses the field-name mapping done by to_dict().
        """
        from mimic_bhc.structure import StructureResult

        structure = None
        if d.get("bhc_structure") is not None:
            s = d["bhc_structure"]
            structure = StructureResult(
                category=s["category"],
                numbering_convention=s.get("numbering_convention"),
                modifiers=tuple(s.get("modifiers", ())),
                nicu_domain_count=s.get("nicu_domain_count", 0),
                labeled_section_count=s.get("labeled_section_count", 0),
            )

        return cls(
            success=d["extraction_success"],
            bhc_text=d.get("bhc_text"),
            start_header=d.get("start_header"),
            end_header=d.get("end_header"),
            failure_reason=d.get("failure_reason"),
            bhc_start=d.get("bhc_start"),
            bhc_end=d.get("bhc_end"),
            hadm_id=d.get("hadm_id"),
            note_row_id=d.get("row_id"),
            subject_id=d.get("subject_id"),
            chartdate=d.get("chartdate"),
            note_length=d.get("note_length"),
            note_text=d.get("note_text"),
            note_row_count=d.get("note_row_count"),
            quality_flags=tuple(d.get("quality_flags", ())),
            bhc_structure=structure,
        )
