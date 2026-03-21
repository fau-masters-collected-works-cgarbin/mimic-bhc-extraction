"""Command-line interface for BHC extraction and header discovery."""

from __future__ import annotations

import argparse
import sys

from mimic_bhc.discovery import discover_headers_from_file
from mimic_bhc.pipeline import extract_from_file


def _run_extract(args: argparse.Namespace) -> int:
    """Run the extract subcommand."""
    extract_from_file(
        input_path=args.input,
        output_path=args.output,
        format_hint=args.format,
        pretty_print=args.pretty,
        sample_size=args.sample,
    )
    print(f"BHC extraction complete. Output written to {args.output}")
    return 0


def _run_discover(args: argparse.Namespace) -> int:
    """Run the discover subcommand."""
    results = discover_headers_from_file(
        input_path=args.input,
        format_hint=args.format,
        sample_size=args.sample,
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
        "--sample",
        type=int,
        default=500,
        help="Number of notes to sample (default: 500, 0 for all notes)",
    )


_SUBCOMMANDS = {"extract", "discover"}


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

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
