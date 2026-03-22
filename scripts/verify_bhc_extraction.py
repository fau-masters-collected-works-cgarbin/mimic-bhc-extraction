"""Diagnostic verification of BHC extraction quality.

Reads persisted extraction results (JSONL with note text included)
and runs two diagnostic checks:
1. Categorize extraction failures (why do notes fail?)
2. Spot-check extraction boundaries on successful extractions

For aggregate statistics (success rates, header distributions, length
stats), use the extraction package's stats command instead:
    mimic-bhc-extract stats --input results/bhc_extraction_mimic-iii.jsonl

Usage:
    uv run python scripts/verify_bhc_extraction.py --input results/bhc_extraction_mimic-iii.jsonl
"""

from __future__ import annotations

import argparse
import re
import sys

from mimic_bhc.io import load_results
from mimic_bhc.patterns import END_BOUNDARY_RE


def separator(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


# ---------------------------------------------------------------------------
# Task 1: Categorize failures
# ---------------------------------------------------------------------------
def categorize_failures(results, n_inspect=20):
    """Inspect failed extractions using note text from the extraction results."""
    separator("TASK 1: Categorize Extraction Failures")

    failed = [r for r in results if not r.extraction_success]
    total = len(results)
    successes = total - len(failed)
    pct = len(failed) / total
    print(f"Total: {total}, Successes: {successes}, Failures: {len(failed)} ({pct:.1%})")

    if not failed:
        print("No failures to inspect.")
        return

    inspect_set = failed[:n_inspect]

    categories: dict[str, list] = {
        "no_section_headers_at_all": [],
        "has_course_keyword_not_on_own_line": [],
        "has_alternate_header": [],
        "very_short_note": [],
        "addendum_only": [],
        "other": [],
    }

    course_re = re.compile(r"(?:brief\s+)?hosp(?:ital)?\s+course", re.IGNORECASE)
    header_re = re.compile(r"^[ \t]*[A-Z][A-Za-z /\-]+:?\s*$", re.MULTILINE)

    print(f"\nInspecting {len(inspect_set)} failed notes:\n")

    for i, r in enumerate(inspect_set):
        note_text = r.note_text or ""
        note_len = len(note_text)

        print(
            f"--- Failure #{i + 1}: ROW_ID={r.row_id}, HADM_ID={r.hadm_id}, length={note_len} ---"
        )

        if not note_text:
            categories["other"].append(r.row_id)
            print("  Category: NO NOTE TEXT (not included in extraction output)")
            print()
            continue

        min_note_length = 200
        if note_len < min_note_length:
            categories["very_short_note"].append(r.row_id)
            print(f"  Category: VERY SHORT NOTE ({note_len} chars)")
            print(f"  Full text: {note_text[:200]!r}")
            print()
            continue

        first_500 = note_text[:500].lower()
        if "addendum" in first_500 and "hospital course" not in note_text.lower():
            categories["addendum_only"].append(r.row_id)
            print("  Category: ADDENDUM (no BHC content)")
            print(f"  First 200 chars: {note_text[:200]!r}")
            print()
            continue

        course_matches = list(course_re.finditer(note_text))
        if course_matches:
            categories["has_course_keyword_not_on_own_line"].append(r.row_id)
            print(
                f"  Category: HAS 'COURSE' KEYWORD but not matched "
                f"({len(course_matches)} occurrences)"
            )
            for m in course_matches[:3]:
                start = max(0, m.start() - 40)
                end = min(len(note_text), m.end() + 40)
                context = note_text[start:end].replace("\n", "\\n")
                print(f"  Context: ...{context!r}...")
            print()
            continue

        headers_found = header_re.findall(note_text)
        if not headers_found:
            categories["no_section_headers_at_all"].append(r.row_id)
            print("  Category: NO SECTION HEADERS AT ALL (unstructured note)")
            print(f"  First 300 chars: {note_text[:300]!r}")
            print()
            continue

        alternate = [
            h.strip()
            for h in headers_found
            if any(kw in h.lower() for kw in ["course", "summary", "narrative", "hospital"])
        ]
        if alternate:
            categories["has_alternate_header"].append(r.row_id)
            print(f"  Category: HAS ALTERNATE HEADER: {alternate}")
        else:
            categories["other"].append(r.row_id)
            print("  Category: OTHER (has headers but none match BHC)")
            print(f"  Headers found: {[h.strip() for h in headers_found[:10]]}")

        print(f"  First 300 chars: {note_text[:300]!r}")
        print()

    print("--- FAILURE CATEGORY SUMMARY ---")
    for cat, ids in categories.items():
        if ids:
            print(f"  {cat}: {len(ids)} notes")
    print(f"  TOTAL inspected: {sum(len(v) for v in categories.values())}")


# ---------------------------------------------------------------------------
# Task 2: Spot-check boundaries
# ---------------------------------------------------------------------------
def spot_check_boundaries(results, n_check=10):
    """Verify extraction boundaries using note text from the extraction results."""
    separator("TASK 2: Spot-Check Extraction Boundaries")

    import random as rand

    successful = [r for r in results if r.extraction_success and r.note_text]
    rng = rand.Random(42)  # noqa: S311
    sample = rng.sample(successful, min(n_check, len(successful)))

    issues_found = 0

    for i, r in enumerate(sample):
        note_text = r.note_text
        bhc_text = r.bhc_text

        print(f"--- Check #{i + 1}: ROW_ID={r.row_id}, HADM_ID={r.hadm_id} ---")
        print(f"  Start header: {r.start_header}")
        print(f"  End boundary: {r.end_header}")
        print(f"  BHC length: {r.bhc_char_count} chars")
        cat = r.bhc_structure.category if r.bhc_structure else "?"
        print(f"  Structure: {cat}")

        bhc_start_idx = note_text.find(bhc_text[:100])
        if bhc_start_idx == -1:
            print("  WARNING: Could not locate BHC in original note!")
            issues_found += 1
            continue

        bhc_end_idx = bhc_start_idx + len(bhc_text)

        pre_start = max(0, bhc_start_idx - 100)
        pre_context = note_text[pre_start:bhc_start_idx].replace("\n", "\\n")
        print(f"  Pre-BHC: ...{pre_context!r}")
        print(f"  BHC starts: {bhc_text[:150]!r}")
        print(f"  BHC ends: ...{bhc_text[-150:]!r}")

        post_end = min(len(note_text), bhc_end_idx + 150)
        post_context = note_text[bhc_end_idx:post_end].replace("\n", "\\n")
        print(f"  Post-BHC: {post_context!r}")

        issues = []
        internal_boundary = END_BOUNDARY_RE.search(bhc_text)
        if internal_boundary:
            issues.append(f"Internal end-boundary match: {internal_boundary.group(0).strip()!r}")

        if issues:
            issues_found += len(issues)
            print(f"  ISSUES: {issues}")
        else:
            print("  OK")
        print()

    print(f"--- {issues_found} issue(s) found across {len(sample)} checks ---")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        required=True,
        help="Path to extraction results JSONL (must include note_text)",
    )
    args = parser.parse_args()

    results = load_results(args.input)
    if not results:
        print("ERROR: No extraction results found.")
        sys.exit(1)
    print(f"Loaded {len(results):,} extraction results")

    has_note_text = any(r.note_text for r in results[:10])
    if not has_note_text:
        print("WARNING: note_text not in results. Re-run extraction with --include-note-text.")
        print("Diagnostics require the original note text.")
        sys.exit(1)

    categorize_failures(results)
    spot_check_boundaries(results)

    separator("VERIFICATION COMPLETE")


if __name__ == "__main__":
    main()
