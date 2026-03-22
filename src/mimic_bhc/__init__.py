"""BHC (Brief Hospital Course) extraction from MIMIC discharge summaries.

Public API exports:
- extract_bhc: Extract BHC from a single note
- BHCResult: Result dataclass from extraction
- classify_bhc_structure: Classify BHC organizational structure
- StructureResult: Result dataclass from structure classification
"""

from mimic_bhc.extract import extract_bhc
from mimic_bhc.io import load_jsonl, load_results, write_jsonl, write_results
from mimic_bhc.model import BHCResult
from mimic_bhc.structure import StructureResult, classify_bhc_structure

__all__ = [
    "BHCResult",
    "StructureResult",
    "classify_bhc_structure",
    "extract_bhc",
    "load_jsonl",
    "load_results",
    "write_jsonl",
    "write_results",
]
