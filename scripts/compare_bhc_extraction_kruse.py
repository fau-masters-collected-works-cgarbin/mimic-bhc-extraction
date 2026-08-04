"""Compare our BHC extraction against Kruse et al.'s regex extraction.

Kruse et al. (2025) use a minimal regex approach to extract the Brief
Hospital Course from MIMIC-III discharge summaries. This script loads
our persisted extraction results (JSONL with note text) and runs
Kruse's regex on the same notes to compare coverage and text overlap.

Source: https://github.com/LARK-NLP-Lab/longitudinal_clinical_summarization
File:   evaluation/evaluate_ds.py, lines 41-73 (is_ground_truth=True branch)

Usage:
    uv run python scripts/compare_bhc_extraction_kruse.py \
        --input results/bhc_extraction_mimic-iii.jsonl
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

from mimic_bhc.io import load_results

# Thresholds for text overlap reporting
_LOW_OVERLAP_THRESHOLD = 0.5
_HIGH_OVERLAP_THRESHOLD = 0.99

# Labels for why Kruse returned nothing on a note we extracted.
_CAUSE_NO_START = "start marker missing"
_CAUSE_NO_END = "start matched, end marker missing"
_CAUSE_ORDER = "both markers present, end precedes start"


# ---------------------------------------------------------------------------
# Kruse et al. extraction (verbatim logic)
# ---------------------------------------------------------------------------
# Adapted from evaluate_ds.py::extract_sections(), is_ground_truth=True
# branch. Only the Hospital Course extraction is reproduced here.
# The original function also extracts Diagnosis and Discharge Instructions,
# which are not needed for this comparison.
#
# Behavior: returns the text between the first matching start header and
# the first matching end header. Returns empty string if either is missing.
#
# Known limitation (present in the original code): the end-header search
# starts from position 0, not from after the start match. If an end header
# appears before the BHC start header, the extraction would be incorrect.
# We reproduce this behavior intentionally to keep the comparison faithful
# to the original implementation. In practice the end markers almost always
# follow BHC in MIMIC-III discharge summaries, so this rarely triggers.
_KRUSE_START_RE = re.compile(r"(Brief Hospital Course:|HOSPITAL COURSE:)")
_KRUSE_END_RE = re.compile(
    r"(Discharge Medications:|DISCHARGE STATUS:|Medications on Admission:"
    r"|CONDITION ON DISCHARGE:|DISCHARGE DIAGNOSES:|DISCHARGE CONDITION:)"
)


@dataclass(frozen=True)
class KruseResult:
    """Kruse's extraction, plus which of its two markers matched.

    The marker flags are what let the report say why an extraction came back
    empty, which a bare text result cannot distinguish.
    """

    text: str
    start_found: bool
    end_found: bool

    @property
    def cause(self) -> str:
        """Label why the extraction is empty. Meaningless when text is non-empty."""
        if not self.start_found:
            return _CAUSE_NO_START
        return _CAUSE_ORDER if self.end_found else _CAUSE_NO_END


def kruse_extract_hospital_course(text: str) -> KruseResult:
    """Extract Hospital Course using Kruse et al.'s regex logic."""
    hospital_course_start = _KRUSE_START_RE.search(text)
    hospital_course_end = _KRUSE_END_RE.search(text)
    bhc = ""
    if hospital_course_start and hospital_course_end:
        bhc = text[hospital_course_start.end() : hospital_course_end.start()].strip()
    return KruseResult(
        text=bhc,
        start_found=hospital_course_start is not None,
        end_found=hospital_course_end is not None,
    )


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
def run_comparison(input_path: str) -> pd.DataFrame:
    """Load our results from JSONL, run Kruse on the same note text, compare."""
    results = load_results(input_path)
    if not results:
        print("ERROR: No extraction results found.")
        sys.exit(1)

    has_note_text = any(r.note_text for r in results[:10])
    if not has_note_text:
        print("ERROR: note_text not in results. Re-run extraction with --include-note-text.")
        sys.exit(1)

    print(f"  Loaded {len(results):,} extraction results from JSONL")

    # Run Kruse's extractor on the same note text used by our extractor, so
    # both see identical input.
    print("  Running Kruse extraction...")
    t0 = time.time()
    rows = []
    for r in results:
        note_text = r.note_text or ""
        kruse = kruse_extract_hospital_course(note_text)

        rows.append(
            {
                "hadm_id": r.hadm_id,
                "ours_success": r.extraction_success,
                "ours_bhc_text": r.bhc_text or "",
                "ours_bhc_length": r.bhc_char_count,
                "ours_start_header": r.start_header,
                "ours_end_header": r.end_header,
                "ours_failure_reason": r.failure_reason,
                "kruse_success": len(kruse.text) > 0,
                "kruse_bhc_length": len(kruse.text),
                "kruse_bhc_text": kruse.text,
                "kruse_cause": kruse.cause,
            }
        )
    print(f"  Kruse extraction done in {time.time() - t0:.1f}s")

    df = pd.DataFrame(rows)

    # Compute agreement.
    df["agreement"] = "both_fail"
    df.loc[df["ours_success"] & df["kruse_success"], "agreement"] = "both_success"
    df.loc[df["ours_success"] & ~df["kruse_success"], "agreement"] = "ours_only"
    df.loc[~df["ours_success"] & df["kruse_success"], "agreement"] = "kruse_only"

    # Text overlap when both succeed.
    both_mask = df["agreement"] == "both_success"
    both_count = both_mask.sum()
    print(f"  Computing text overlap for {both_count:,} shared successes...")
    t0 = time.time()
    overlap = pd.Series(None, index=df.index, dtype="float64")
    for idx in df.index[both_mask]:
        overlap.loc[idx] = SequenceMatcher(
            None, df.loc[idx, "ours_bhc_text"], df.loc[idx, "kruse_bhc_text"]
        ).ratio()
    df["text_overlap"] = overlap
    print(f"  Overlap computed in {time.time() - t0:.1f}s")

    df["length_diff"] = None
    df.loc[both_mask, "length_diff"] = (
        df.loc[both_mask, "ours_bhc_length"] - df.loc[both_mask, "kruse_bhc_length"]
    )

    return df


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def print_report(df: pd.DataFrame) -> None:
    """Print comparison summary."""
    total = len(df)
    print(f"\n{'=' * 70}")
    print("  BHC Extraction Comparison: Ours vs. Kruse et al.")
    print(f"{'=' * 70}")
    print(f"\n  Total admissions: {total:,}")

    ours_success = df["ours_success"].sum()
    kruse_success = df["kruse_success"].sum()
    print("\n  Success rates:")
    print(f"    Ours:  {ours_success:,} / {total:,} ({100 * ours_success / total:.1f}%)")
    print(f"    Kruse: {kruse_success:,} / {total:,} ({100 * kruse_success / total:.1f}%)")

    agreement = df["agreement"].value_counts()
    print("\n  Agreement matrix:")
    for cat in ["both_success", "ours_only", "kruse_only", "both_fail"]:
        count = agreement.get(cat, 0)
        print(f"    {cat:15s}: {count:,} ({100 * count / total:.1f}%)")

    ours_only = df[df["agreement"] == "ours_only"]
    if len(ours_only) > 0:
        n = len(ours_only)
        print(f"\n  Ours-only successes ({n:,} cases):")
        print("  Why Kruse returned nothing:")
        for cause, count in ours_only["kruse_cause"].value_counts().items():
            print(f"    {cause:45s}: {count:,} ({100 * count / n:.1f}%)")

        no_start = ours_only[ours_only["kruse_cause"] == _CAUSE_NO_START]
        if len(no_start) > 0:
            print(f"\n  Start headers their two literals cannot reach ({len(no_start):,} cases):")
            for header, count in no_start["ours_start_header"].value_counts().head(10).items():
                print(f"    {header}: {count:,}")

        no_end = ours_only[ours_only["kruse_cause"] == _CAUSE_NO_END]
        if len(no_end) > 0:
            print(f"\n  Our end boundary where their end literals missed ({len(no_end):,} cases):")
            boundaries = no_end["ours_end_header"].fillna("(unbounded, we run to end of note)")
            for boundary, count in boundaries.value_counts().items():
                print(f"    {boundary}: {count:,}")

    kruse_only_df = df[df["agreement"] == "kruse_only"]
    if len(kruse_only_df) > 0:
        print(f"\n  Kruse-only successes ({len(kruse_only_df):,} cases):")
        print("  Our failure reasons:")
        for reason, count in kruse_only_df["ours_failure_reason"].value_counts().items():
            print(f"    {reason}: {count:,}")

    both = df[df["agreement"] == "both_success"]
    if len(both) > 0:
        overlap = both["text_overlap"]
        length_diff = both["length_diff"]
        print(f"\n  Both-success cases ({len(both):,}):")
        print("    Text overlap (SequenceMatcher ratio):")
        print(f"      Mean:   {overlap.mean():.3f}")
        print(f"      Median: {overlap.median():.3f}")
        print(f"      P5:     {overlap.quantile(0.05):.3f}")
        print("    Length difference (ours - Kruse, chars):")
        print(f"      Mean:   {length_diff.mean():.0f}")
        print(f"      Median: {length_diff.median():.0f}")

        low_overlap = both[both["text_overlap"] < _LOW_OVERLAP_THRESHOLD]
        if len(low_overlap) > 0:
            print(f"\n    Low overlap (<{_LOW_OVERLAP_THRESHOLD}): {len(low_overlap):,} cases")

        high_overlap = both[both["text_overlap"] > _HIGH_OVERLAP_THRESHOLD]
        pct = 100 * len(high_overlap) / len(both)
        print(
            f"    Near-exact match (>{_HIGH_OVERLAP_THRESHOLD}): {len(high_overlap):,} ({pct:.1f}%)"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        required=True,
        help="Path to extraction results JSONL (must include note_text)",
    )
    parser.add_argument(
        "--save-details",
        action="store_true",
        help="Save per-admission comparison CSV alongside the input file",
    )
    args = parser.parse_args()

    df = run_comparison(args.input)
    print_report(df)

    if args.save_details:
        out_df = df.drop(columns=["ours_bhc_text", "kruse_bhc_text"])
        input_stem = Path(args.input).stem.replace("bhc_extraction_", "")
        out_path = Path(args.input).parent / f"bhc_extraction_comparison_{input_stem}.csv"
        out_df.to_csv(out_path, index=False)
        print(f"\n  Detailed results saved to {out_path}")


if __name__ == "__main__":
    main()
