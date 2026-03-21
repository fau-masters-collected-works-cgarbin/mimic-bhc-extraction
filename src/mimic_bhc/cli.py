"""Command-line interface for BHC extraction and header discovery."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from mimic_bhc.discovery import discover_headers_from_file
from mimic_bhc.pipeline import extract_from_file

if TYPE_CHECKING:
    from mimic_bhc.model import BHCResult


def _write_results(results: list[BHCResult], output_path: str, *, pretty: bool) -> None:
    """Write extraction results to a JSON/JSONL file."""
    dicts = [r.to_dict() for r in results]
    with open(output_path, "w") as f:  # noqa: PTH123
        if pretty:
            json.dump(dicts, f, indent=2)
            f.write("\n")
        else:
            for d in dicts:
                json.dump(d, f)
                f.write("\n")


def _run_extract(args: argparse.Namespace) -> int:
    """Run the extract subcommand."""
    results = extract_from_file(
        input_path=args.input,
        format_hint=args.format,
        sample_size=args.sample,
    )
    _write_results(results, args.output, pretty=args.pretty)
    print(f"BHC extraction complete. Output written to {args.output}")
    return 0


def _run_discover(args: argparse.Namespace) -> int:
    """Run the discover subcommand."""
    results = discover_headers_from_file(
        input_path=args.input,
        format_hint=args.format,
        sample_size=args.notes,
    )

    if not results:
        print("No section headers found.")
        return 0

    # Column widths
    max_header = max(len(r[0]) for r in results)
    max_count = max(len(str(r[1])) for r in results)
    header_w = max(max_header, len("Header"))
    count_w = max(max_count, len("Count"))

    print(f"{'Header':<{header_w}}  {'Count':>{count_w}}  Role")
    print(f"{'-' * header_w}  {'-' * count_w}  ----")
    for header, count, role in results:
        print(f"{header:<{header_w}}  {count:>{count_w}}  {role}")

    return 0


def _load_results(input_path: str) -> list[dict]:
    """Load extraction results from a JSON or JSONL file."""
    with open(input_path) as f:  # noqa: PTH123
        first_char = f.read(1)
        if not first_char:
            return []
        f.seek(0)
        if first_char == "[":
            return json.load(f)
        return [json.loads(line) for line in f if line.strip()]


def _run_stats(args: argparse.Namespace) -> int:  # noqa: C901
    """Run the stats subcommand."""
    from mimic_bhc.model import BHCResult
    from mimic_bhc.stats import compute_bhc_stats

    records = _load_results(args.input)
    if not records:
        print("No records found.")
        return 0

    results = [BHCResult.from_dict(rec) for rec in records]
    stats = compute_bhc_stats(results)

    # Summary
    pct = stats["success_rate"] * 100
    print(f"Total admissions:  {stats['total']:,}")
    print(f"Successful:        {stats['successes']:,} ({pct:.1f}%)")
    print(f"Failed:            {stats['failures']:,} ({100 - pct:.1f}%)")

    # Failure reasons
    if stats["failure_reasons"]:
        print("\nFailure reasons:")
        for reason, count in sorted(stats["failure_reasons"].items(), key=lambda x: -x[1]):
            print(f"  {reason:<20} {count:>8,}")

    # Start header distribution
    if stats["header_distribution"]:
        print("\nStart headers:")
        for header, count in sorted(stats["header_distribution"].items(), key=lambda x: -x[1]):
            print(f"  {header:<40} {count:>8,}")

    # End header distribution
    if stats["end_header_distribution"]:
        print("\nEnd headers:")
        for header, count in sorted(stats["end_header_distribution"].items(), key=lambda x: -x[1]):
            print(f"  {header:<40} {count:>8,}")
    if stats["unbounded_extractions"] > 0:
        print(f"  {'(unbounded)':<40} {stats['unbounded_extractions']:>8,}")

    # BHC length stats
    length = stats["bhc_length_stats"]
    if length:
        print("\nBHC length (characters):")
        print(
            f"  Median: {length['median']:,.0f}  Mean: {length['mean']:,.0f}"
            f"  Q25: {length['q25']:,.0f}  Q75: {length['q75']:,.0f}"
        )
        print(f"  Min: {length['min']:,}  Max: {length['max']:,}")
    if stats["short_extractions"] > 0:
        print(f"  Short extractions (<100 chars): {stats['short_extractions']:,}")

    # Structure distribution
    if stats["format_distribution"]:
        print("\nStructure distribution:")
        for fmt, count in sorted(stats["format_distribution"].items(), key=lambda x: -x[1]):
            print(f"  {fmt:<40} {count:>8,}")

    return 0


def _add_extract_args(parser: argparse.ArgumentParser) -> None:
    """Add arguments for the extract subcommand."""
    parser.add_argument(
        "--input",
        required=True,
        help="Path to MIMIC notes file: NOTEEVENTS.csv.gz (MIMIC-III) "
        "or discharge.csv.gz (MIMIC-IV-Note)",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write JSONL output (one JSON object per admission per line)",
    )
    parser.add_argument(
        "--format",
        choices=["mimic3", "mimic4", "auto"],
        default="auto",
        help="Input format (default: auto-detect by checking for CATEGORY column)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Write a single pretty-printed JSON array instead of JSONL",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Extract from random sample of N admissions (optional, for testing)",
    )


def _add_discover_args(parser: argparse.ArgumentParser) -> None:
    """Add arguments for the discover subcommand."""
    parser.add_argument(
        "--input",
        required=True,
        help="Path to MIMIC notes file: NOTEEVENTS.csv.gz (MIMIC-III) "
        "or discharge.csv.gz (MIMIC-IV-Note)",
    )
    parser.add_argument(
        "--format",
        choices=["mimic3", "mimic4", "auto"],
        default="auto",
        help="Input format (default: auto-detect by checking for CATEGORY column)",
    )
    parser.add_argument(
        "--notes",
        type=int,
        default=500,
        help="Number of discharge notes to scan for headers (default: 500, 0 for all)",
    )


def _add_stats_args(parser: argparse.ArgumentParser) -> None:
    """Add arguments for the stats subcommand."""
    parser.add_argument(
        "--input",
        required=True,
        help="Path to extraction output file (JSON or JSONL from the extract command)",
    )


_SUBCOMMANDS = {"extract", "discover", "stats"}


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="mimic-bhc-extract",
        description="Extract Brief Hospital Course sections from MIMIC discharge summaries.",
    )

    subparsers = parser.add_subparsers(dest="command")

    # extract subcommand
    extract_parser = subparsers.add_parser(
        "extract",
        help="Extract BHC sections from a MIMIC notes file",
    )
    _add_extract_args(extract_parser)

    # discover subcommand
    discover_parser = subparsers.add_parser(
        "discover",
        help="Discover section headers in a MIMIC notes file",
    )
    _add_discover_args(discover_parser)

    # stats subcommand
    stats_parser = subparsers.add_parser(
        "stats",
        help="Show summary statistics from extraction output",
    )
    _add_stats_args(stats_parser)

    # Backward compatibility: if first arg is a flag like --input (not a
    # known subcommand and not --help/-h), prepend "extract" so the old
    # single-command CLI still works.
    argv = sys.argv[1:]
    if argv and argv[0] not in _SUBCOMMANDS and argv[0] not in ("-h", "--help"):
        argv = ["extract", *argv]

    args = parser.parse_args(argv)

    if args.command == "extract":
        return _run_extract(args)
    if args.command == "discover":
        return _run_discover(args)
    if args.command == "stats":
        return _run_stats(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
