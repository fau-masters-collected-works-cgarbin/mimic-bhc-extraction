"""Generate BHC extraction audit files for manual review.

Reads persisted extraction results (JSONL) and produces an audit file
with review flags and fields for human review status.

Usage:
    uv run python scripts/generate_bhc_audit.py \
        --input results/bhc_extraction_mimic-iii.jsonl \
        --output results/bhc_audit_mimic-iii.jsonl
"""

from __future__ import annotations

import argparse
import sys

from mimic_bhc.audit import build_audit_entries
from mimic_bhc.io import load_results, write_jsonl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        required=True,
        help="Path to extraction results JSONL",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write audit JSONL",
    )
    args = parser.parse_args()

    print(f"Loading extraction results from {args.input}...")
    results = load_results(args.input)
    if not results:
        print("ERROR: No extraction results found.")
        sys.exit(1)

    successes = sum(1 for r in results if r.extraction_success)
    print(f"  {len(results):,} admissions, {successes:,} successful")

    print("Building audit entries...")
    entries = build_audit_entries(results)

    flagged = sum(1 for e in entries if e["review_flags"])
    print(f"  {flagged:,} entries flagged for review")

    flag_counts: dict[str, int] = {}
    for e in entries:
        for f in e["review_flags"]:
            flag_counts[f] = flag_counts.get(f, 0) + 1
    for flag, count in sorted(flag_counts.items(), key=lambda x: -x[1]):
        print(f"    {flag}: {count:,}")

    write_jsonl(entries, args.output)
    print(f"\nWrote {len(entries):,} entries to {args.output}")


if __name__ == "__main__":
    main()
