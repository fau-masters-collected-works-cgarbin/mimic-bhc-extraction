"""Command-line interface for BHC extraction and header discovery."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, TypedDict

from tabulate import tabulate

from mimic_bhc.batch import extract_from_file
from mimic_bhc.discovery import discover_headers_from_file
from mimic_bhc.io import write_results
from mimic_bhc.population_stats import (
    BUCKET_COLUMN_CAREVUE,
    BUCKET_COLUMN_METAVISION,
    BUCKET_COLUMN_TOTAL,
    CLINICAL_NARRATIVE,
    IMAGING_ONLY,
)

if TYPE_CHECKING:
    from mimic_bhc.population_stats import (
        BucketedAvailability,
        DistributionStats,
        FormatByNoteCategory,
        LosStats,
        MortalityStats,
        NoteCategoryAvailability,
        NoteVolumeStats,
        PatientConcentration,
    )
    from mimic_bhc.stats import BHCStats

_TABLE_INDENT = "    "
_TABLE_FMT = "simple"
_BUCKET_EXCLUSION_NOTE = (
    '("All count" sums CareVue, MetaVision, Mixed, and No-ICU. '
    'Mixed and No-ICU are itemized in the "By ICU system" block above.)'
)
_BUCKET_PRINT_COLUMNS: tuple[str, ...] = (
    BUCKET_COLUMN_TOTAL,
    BUCKET_COLUMN_CAREVUE,
    BUCKET_COLUMN_METAVISION,
)


class _ResolvedMimicPaths(TypedDict):
    """Internal: MIMIC table paths after resolving --mimic-dir / per-table flags."""

    admissions: str
    noteevents: str
    icustays: str | None


def _run_extract(args: argparse.Namespace) -> int:
    """Run the extract subcommand."""
    results = extract_from_file(
        input_path=args.input,
        format_hint=args.format,
        sample_size=args.sample,
        include_note_text=args.include_note_text,
    )
    write_results(results, args.output, pretty=args.pretty)
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


def _run_stats(args: argparse.Namespace) -> int:
    """Run the stats subcommand."""
    from mimic_bhc.io import load_results
    from mimic_bhc.population_stats import (
        compute_patient_concentration,
        compute_population_stats,
    )
    from mimic_bhc.stats import compute_bhc_stats

    results = load_results(args.input)
    if not results:
        print("No records found.")
        return 0
    stats = compute_bhc_stats(results)

    _print_extraction_diagnostics(stats)
    _print_bhc_text_properties(stats)

    paths = _resolve_mimic_paths(args)
    pop_stats = None
    if paths:
        pop_stats = compute_population_stats(
            results,
            admissions_path=paths["admissions"],
            noteevents_path=paths["noteevents"],
            icustays_path=paths["icustays"],
        )
        _print_note_category_availability(pop_stats["note_category_availability"])
        _print_los(pop_stats["los_distribution"])
        _print_mortality(pop_stats["mortality"])
    _print_patient_concentration(compute_patient_concentration(results))

    if pop_stats is not None:
        _print_note_volume(pop_stats["note_volume"])
        _print_format_cross_tab(pop_stats["format_by_note_category"])

    return 0


def _print_extraction_diagnostics(stats: BHCStats) -> None:
    pct = stats["success_rate"] * 100
    print(f"Total admissions:  {stats['total']:,}")
    print(f"Successful:        {stats['successes']:,} ({pct:.1f}%)")
    print(f"Failed:            {stats['failures']:,} ({100 - pct:.1f}%)")

    if stats["failure_reasons"]:
        print("\nFailure reasons:")
        for reason, count in sorted(stats["failure_reasons"].items(), key=lambda x: -x[1]):
            print(f"  {reason:<20} {count:>8,}")

    successes = stats["successes"]
    if stats["header_distribution"]:
        print("\nStart headers:")
        for header, count in sorted(stats["header_distribution"].items(), key=lambda x: -x[1]):
            print(f"  {header:<40} {count:>8,} ({100 * count / successes:>5.1f}%)")

    if stats["end_header_distribution"]:
        print("\nEnd headers:")
        for header, count in sorted(stats["end_header_distribution"].items(), key=lambda x: -x[1]):
            print(f"  {header:<40} {count:>8,} ({100 * count / successes:>5.1f}%)")
    if stats["unbounded_extractions"] > 0:
        ub = stats["unbounded_extractions"]
        print(f"  {'(unbounded)':<40} {ub:>8,} ({100 * ub / successes:>5.1f}%)")


def _print_bhc_text_properties(stats: BHCStats) -> None:
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

    successes = stats["successes"]
    if stats["format_distribution"]:
        print("\nStructure distribution:")
        for fmt, count in sorted(stats["format_distribution"].items(), key=lambda x: -x[1]):
            print(f"  {fmt:<40} {count:>8,} ({100 * count / successes:>5.1f}%)")


def _resolve_mimic_paths(args: argparse.Namespace) -> _ResolvedMimicPaths | None:
    """Resolve --mimic-dir or per-table flags to a path dict.

    Returns ``None`` when no MIMIC source is supplied (population stats are
    skipped in that case). Raises ``SystemExit`` when only one of the two
    required tables (ADMISSIONS, NOTEEVENTS) can be located.
    """
    from mimic_bhc.population_stats import discover_mimic3_paths

    admissions = getattr(args, "admissions", None)
    noteevents = getattr(args, "noteevents", None)
    icustays = getattr(args, "icustays", None)
    mimic_dir = getattr(args, "mimic_dir", None)

    if mimic_dir:
        discovered = discover_mimic3_paths(mimic_dir)
        admissions = admissions or discovered["admissions"]
        noteevents = noteevents or discovered["noteevents"]
        icustays = icustays or discovered["icustays"]

    if not admissions and not noteevents:
        return None

    if not admissions or not noteevents:
        missing = [
            name
            for name, value in (("admissions", admissions), ("noteevents", noteevents))
            if not value
        ]
        names = ", ".join(missing)
        print(
            f"Population stats need {names}; pass --mimic-dir or the per-table flag(s).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    return {"admissions": admissions, "noteevents": noteevents, "icustays": icustays}


def _print_note_category_availability(nca: NoteCategoryAvailability) -> None:
    total_nca = nca["total"]
    print("\nNote-category availability:")
    for label in ("clinical_narrative", "imaging_only"):
        count = nca["counts"][label]
        pct = 100 * count / total_nca if total_nca else 0.0
        print(f"  {label:<40} {count:>8,} ({pct:>5.1f}%)")
    print(f"  {'Total':<40} {total_nca:>8,}")

    by_dbsource = nca["by_dbsource"]
    if by_dbsource:
        ordered = [k for k in ("carevue", "metavision") if k in by_dbsource]
        ordered.extend(k for k in by_dbsource if k not in {"carevue", "metavision"})

        print("\n  By ICU system:")
        for source in ordered:
            sub = by_dbsource[source]
            sub_total = sub["clinical_narrative"] + sub["imaging_only"]
            display = {
                "carevue": "CareVue",
                "metavision": "MetaVision",
                "mixed": "Mixed (carevue + metavision)",
                "no_icu": "No ICU stay",
            }.get(source, source)
            print(f"    {display}")
            for label in ("clinical_narrative", "imaging_only"):
                count = sub[label]
                pct = 100 * count / sub_total if sub_total else 0.0
                print(f"      {label:<36} {count:>8,} ({pct:>5.1f}%)")

    if nca["by_los_bucket"] is not None:
        _print_bucketed_availability(
            "By LOS bucket (clinical_narrative % within each bucket and system):",
            nca["by_los_bucket"],
        )
    if nca["by_bhc_length_bucket"] is not None:
        _print_bucketed_availability(
            "By BHC length bucket (clinical_narrative % within each bucket and system):",
            nca["by_bhc_length_bucket"],
        )


def _print_los(los: LosStats) -> None:
    los_total = los["total"]
    print("\nLength of stay (days):")
    print(
        f"  Median: {los['median']:.1f}  Mean: {los['mean']:.1f}"
        f"  Q25: {los['p25']:.1f}  Q75: {los['p75']:.1f}  Q90: {los['p90']:.1f}"
    )
    short_los_pct = 100 * los["lt_3_days"] / los_total if los_total else 0.0
    print(
        f"  {'Admissions with LOS < 3 days:':<40} {los['lt_3_days']:>8,} ({short_los_pct:>5.1f}%)"
    )


def _print_mortality(mort: MortalityStats) -> None:
    print("\nMortality:")
    mort_total = mort["total"]
    mort_pct = 100 * mort["deaths"] / mort_total if mort_total else 0.0
    print(
        f"  {'Hospital deaths (HOSPITAL_EXPIRE_FLAG=1):':<40} "
        f"{mort['deaths']:>8,} ({mort_pct:>5.1f}%)"
    )


def _print_note_volume(volume: NoteVolumeStats) -> None:
    print(
        "\nSource-record content per admission by note-category class (discharge summary excluded):"
    )
    _print_volume_table("Note count per admission:", volume["note_count"])
    _print_volume_table("Note characters per admission:", volume["note_chars"])
    print(f"{_TABLE_INDENT}{_BUCKET_EXCLUSION_NOTE}")


def _print_volume_table(title: str, table: dict[str, dict[str, DistributionStats]]) -> None:
    print(f"\n  {title}")
    headers = ["System", "Class", "Median", "Mean", "Q25", "Q75", "Q90", "Min", "Max", "n"]
    system_labels = (
        (BUCKET_COLUMN_TOTAL, "All systems"),
        (BUCKET_COLUMN_CAREVUE, "CareVue"),
        (BUCKET_COLUMN_METAVISION, "MetaVision"),
    )
    class_labels = (
        (CLINICAL_NARRATIVE, "clin_narr"),
        (IMAGING_ONLY, "imaging_only"),
    )
    rows: list[list[str]] = []
    for system_key, system_label in system_labels:
        if system_key not in table:
            continue
        for class_key, class_label in class_labels:
            d = table[system_key][class_key]
            rows.append(
                [
                    system_label,
                    class_label,
                    f"{d['median']:,.1f}",
                    f"{d['mean']:,.1f}",
                    f"{d['p25']:,.1f}",
                    f"{d['p75']:,.1f}",
                    f"{d['p90']:,.1f}",
                    f"{d['min']:,}",
                    f"{d['max']:,}",
                    f"{d['n']:,}",
                ]
            )
    _print_indented_table(rows, headers)


def _print_format_cross_tab(cross: FormatByNoteCategory) -> None:
    cells = cross["cells"]
    row_totals = cross["row_totals"]
    col_totals = cross["col_totals"]
    grand = cross["grand_total"]
    print("\nBHC format by note-category availability (templates excluded):")
    headers = ["Format", "clinical_narrative", "imaging_only", "Total"]
    rows = [
        [
            fmt,
            f"{cells[fmt]['clinical_narrative']:,}",
            f"{cells[fmt]['imaging_only']:,}",
            f"{row_totals[fmt]:,}",
        ]
        for fmt in ("narrative", "domain_organized")
    ]
    rows.append(
        [
            "Total",
            f"{col_totals['clinical_narrative']:,}",
            f"{col_totals['imaging_only']:,}",
            f"{grand:,}",
        ]
    )
    _print_indented_table(rows, headers)


def _print_bucketed_availability(title: str, breakdown: BucketedAvailability) -> None:
    print(f"\n  {title}")
    headers = [
        "Bucket",
        "All count",
        "clin_narr %",
        "CareVue count",
        "clin_narr %",
        "MetaVision count",
        "clin_narr %",
    ]
    column_totals: dict[str, int] = dict.fromkeys(_BUCKET_PRINT_COLUMNS, 0)
    rows: list[list[str]] = []
    for row in breakdown["rows"]:
        cells: list[str] = [row["label"]]
        total = row["total"]["clinical_narrative"] + row["total"]["imaging_only"]
        carevue = row["carevue"]["clinical_narrative"] + row["carevue"]["imaging_only"]
        metavision = row["metavision"]["clinical_narrative"] + row["metavision"]["imaging_only"]
        column_totals[BUCKET_COLUMN_TOTAL] += total
        column_totals[BUCKET_COLUMN_CAREVUE] += carevue
        column_totals[BUCKET_COLUMN_METAVISION] += metavision
        cells.extend([f"{total:,}", _percent_str(row["total"]["clinical_narrative"], total)])
        cells.extend([f"{carevue:,}", _percent_str(row["carevue"]["clinical_narrative"], carevue)])
        cells.extend(
            [f"{metavision:,}", _percent_str(row["metavision"]["clinical_narrative"], metavision)]
        )
        rows.append(cells)

    total_row: list[str] = ["Total"]
    for col in _BUCKET_PRINT_COLUMNS:
        total_row.extend([f"{column_totals[col]:,}", ""])
    rows.append(total_row)

    _print_indented_table(rows, headers)
    print(f"{_TABLE_INDENT}{_BUCKET_EXCLUSION_NOTE}")
    excluded = breakdown["excluded"]
    if any(excluded.values()):
        excluded_str = ", ".join(
            f"{excluded[col]:,} {label}"
            for col, label in (
                (BUCKET_COLUMN_TOTAL, "total"),
                (BUCKET_COLUMN_CAREVUE, "CareVue"),
                (BUCKET_COLUMN_METAVISION, "MetaVision"),
            )
            if excluded[col]
        )
        print(f"{_TABLE_INDENT}({excluded_str} admissions excluded due to missing value)")


def _print_indented_table(rows: list[list[str]], headers: list[str]) -> None:
    # disable_numparse keeps pre-formatted percentage strings like "80.0"
    # intact. Without it tabulate would re-parse them as floats and strip
    # the trailing zero, breaking the .1f alignment across the column.
    table_str = tabulate(
        rows,
        headers=headers,
        tablefmt=_TABLE_FMT,
        disable_numparse=True,
    )
    indented = "\n".join(f"{_TABLE_INDENT}{line}" for line in table_str.splitlines())
    print(indented)


def _percent_str(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.0"
    return f"{100 * numerator / denominator:.1f}"


def _print_patient_concentration(concentration: PatientConcentration) -> None:
    distinct = concentration["distinct_patients"]
    print("\nPatient concentration:")
    print(f"  {'Distinct patients:':<40} {distinct:>8,}")
    for label, key in (
        ("Patients with 1 admission:", "with_1"),
        ("Patients with 2 admissions:", "with_2"),
        ("Patients with 3 admissions:", "with_3"),
        ("Patients with 4+ admissions:", "with_4_plus"),
    ):
        count = concentration[key]
        pct = 100 * count / distinct if distinct else 0.0
        print(f"  {label:<40} {count:>8,} ({pct:>5.1f}%)")


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
    parser.add_argument(
        "--include-note-text",
        action="store_true",
        help="Include the full original note text in each output record "
        "(increases output size significantly)",
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
    parser.add_argument(
        "--mimic-dir",
        default=None,
        help=(
            "Path to MIMIC-III root directory (e.g. physionet.org/files/mimiciii/1.4). "
            "When set, the stats command also reports population baselines "
            "(note-category availability, length of stay, note volume, format x "
            "note-category cross-tab, mortality) by joining ADMISSIONS, NOTEEVENTS, "
            "and ICUSTAYS with the extraction results. ICUSTAYS is optional. Without "
            "it the per-ICU-system columns and the LOS and BHC-length breakdowns are "
            "omitted."
        ),
    )
    parser.add_argument(
        "--admissions",
        default=None,
        help="Explicit path to ADMISSIONS.csv.gz (overrides --mimic-dir).",
    )
    parser.add_argument(
        "--noteevents",
        default=None,
        help="Explicit path to NOTEEVENTS.csv.gz (overrides --mimic-dir).",
    )
    parser.add_argument(
        "--icustays",
        default=None,
        help="Explicit path to ICUSTAYS.csv.gz (overrides --mimic-dir).",
    )


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

    args = parser.parse_args()

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
