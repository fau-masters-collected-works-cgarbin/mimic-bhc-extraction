"""Population-level statistics over the BHC extraction universe.

These statistics extend ``stats.compute_bhc_stats`` with admission-cohort
metadata that lives outside the BHCResult records: length-of-stay, hospital
mortality, note-category availability (presence of nursing or physician notes),
and patient-level concentration.

The universe matches the existing length and structure distribution stats:
admissions where BHC extraction succeeded (``BHCResult.extraction_success``).
No additional quality filtering is applied.

Stats are split into two groups:

- ``compute_patient_concentration``: derived purely from BHCResult.subject_id,
  always available.
- ``load_population_attrs`` + ``compute_population_stats``: require MIMIC source
  tables (ADMISSIONS, NOTEEVENTS, ICUSTAYS) joined to the extraction results.
  ICUSTAYS is optional and only used for the by-ICU-system breakdown.
"""

from __future__ import annotations

import warnings
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import duckdb
import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mimic_bhc.model import BHCResult

# ---------------------------------------------------------------------------
# Note-category classification rule
# ---------------------------------------------------------------------------
# An admission is "clinical_narrative" if any of its notes have CATEGORY in
# {"Nursing", "Nursing/other"} or starts with "Physician". Otherwise it is
# "imaging_only" (the discharge summary plus radiology/ECG-style notes only).
CLINICAL_NARRATIVE = "clinical_narrative"
IMAGING_ONLY = "imaging_only"

# ICU dbsource buckets. "metavision" / "carevue" require every ICU stay to
# come from that system. "mixed" covers admissions with stays in both.
# "no_icu" covers admissions without any ICU stay record (rare in MIMIC-III).
DBSOURCE_CAREVUE = "carevue"
DBSOURCE_METAVISION = "metavision"
DBSOURCE_MIXED = "mixed"
DBSOURCE_NO_ICU = "no_icu"

# Patient concentration: 4+ is the upper bucket the cohort doc uses for the
# tail of the admission-count distribution.
_PATIENT_CONCENTRATION_TAIL_BUCKET = 4

# Length of stay: < 3 days is the slice the cohort filter (LOS >= 3) removes.
_LOS_FILTER_DAYS = 3

# Bucket edges anchored to population LOS quartiles (Q25=4, median=7,
# Q75=13) and BHC-length quartiles (Q25~=1544, median~=2521, Q75~=3940),
# not balanced for equal counts. The long-tail buckets (21+ days,
# 10,000+ chars) are intentionally smaller.
_LOS_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("<3 days", 0, 2),
    ("3 to 4 days", 3, 4),
    ("5 to 6 days", 5, 6),
    ("7 to 9 days", 7, 9),
    ("10 to 13 days", 10, 13),
    ("14 to 20 days", 14, 20),
    ("21+ days", 21, None),
)
_BHC_LENGTH_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("<500 chars", 0, 499),
    ("500 to 999", 500, 999),
    ("1,000 to 1,999", 1000, 1999),
    ("2,000 to 2,999", 2000, 2999),
    ("3,000 to 4,999", 3000, 4999),
    ("5,000 to 9,999", 5000, 9999),
    ("10,000+ chars", 10000, None),
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class PatientConcentration(TypedDict):
    """Distribution of admissions per distinct patient."""

    distinct_patients: int
    with_1: int
    with_2: int
    with_3: int
    with_4_plus: int
    total_admissions: int


class BucketSystemCounts(TypedDict):
    """Note-category counts for one (bucket, ICU system) cell."""

    clinical_narrative: int
    imaging_only: int


BUCKET_COLUMN_TOTAL = "total"
BUCKET_COLUMN_CAREVUE = "carevue"
BUCKET_COLUMN_METAVISION = "metavision"

# Mixed and No-ICU admissions roll into the Total column but get no column
# of their own: their bucketed cells are too sparse to be useful. The
# parent "By ICU system" sub-block still shows their unstratified counts.
_SYSTEM_COLUMNS: tuple[tuple[str, str | None], ...] = (
    (BUCKET_COLUMN_TOTAL, None),
    (BUCKET_COLUMN_CAREVUE, DBSOURCE_CAREVUE),
    (BUCKET_COLUMN_METAVISION, DBSOURCE_METAVISION),
)


class BucketRow(TypedDict):
    """One row of a length-stratified note-category breakdown."""

    label: str
    total: BucketSystemCounts
    carevue: BucketSystemCounts
    metavision: BucketSystemCounts


class BucketedAvailability(TypedDict):
    """Note-category availability stratified by a value bucket, per system.

    Invariant: for each column, ``sum(row[col]) + excluded[col]`` equals
    the matching ``by_dbsource`` row total. The Total column reconciles
    against the population aggregate (which includes Mixed and No-ICU).
    """

    rows: list[BucketRow]
    excluded: dict[str, int]


class NoteCategoryAvailability(TypedDict):
    """Counts of admissions by note-category availability class."""

    counts: dict[str, int]
    total: int
    by_dbsource: dict[str, dict[str, int]] | None
    by_los_bucket: BucketedAvailability | None
    by_bhc_length_bucket: BucketedAvailability | None


class LosStats(TypedDict):
    """Length-of-stay distribution across the BHC-extraction universe."""

    mean: float
    median: float
    p25: float
    p75: float
    p90: float
    lt_3_days: int
    total: int


class FormatByNoteCategory(TypedDict):
    """2x2 cross-tab of BHC format by note-category (templates excluded)."""

    cells: dict[str, dict[str, int]]
    row_totals: dict[str, int]
    col_totals: dict[str, int]
    grand_total: int


class MortalityStats(TypedDict):
    """Hospital-mortality counts (HOSPITAL_EXPIRE_FLAG = 1)."""

    deaths: int
    total: int


class DistributionStats(TypedDict):
    """Distribution summary for one (system, class) cell."""

    median: float
    mean: float
    p25: float
    p75: float
    p90: float
    min: int
    max: int
    n: int


class NoteVolumeStats(TypedDict):
    """Per-admission note volume distributions, by ICU system and note class.

    The ``total`` system bucket is always present. The ``carevue`` and
    ``metavision`` buckets are present only when ICUSTAYS is available.
    """

    note_count: dict[str, dict[str, DistributionStats]]
    note_chars: dict[str, dict[str, DistributionStats]]


class PopulationStats(TypedDict):
    """Bundle of MIMIC-derived population statistics."""

    note_category_availability: NoteCategoryAvailability
    los_distribution: LosStats
    format_by_note_category: FormatByNoteCategory
    mortality: MortalityStats
    note_volume: NoteVolumeStats


# ---------------------------------------------------------------------------
# Patient concentration (BHCResult-only)
# ---------------------------------------------------------------------------


def compute_patient_concentration(results: Sequence[BHCResult]) -> PatientConcentration:
    """Distribution of admissions per distinct patient over successful extractions.

    Universe matches ``compute_population_stats``: successful extractions with a
    non-null hadm_id. Records missing subject_id are kept in ``total_admissions``
    but excluded from the per-patient bucketing (they cannot be attributed to a
    patient).
    """
    successful = [r for r in results if r.extraction_success and r.hadm_id is not None]
    per_patient = Counter(r.subject_id for r in successful if r.subject_id is not None)
    by_count = Counter(per_patient.values())
    return {
        "distinct_patients": len(per_patient),
        "with_1": by_count.get(1, 0),
        "with_2": by_count.get(2, 0),
        "with_3": by_count.get(3, 0),
        "with_4_plus": sum(
            n for k, n in by_count.items() if k >= _PATIENT_CONCENTRATION_TAIL_BUCKET
        ),
        "total_admissions": len(successful),
    }


# ---------------------------------------------------------------------------
# MIMIC source-table loaders (DuckDB-backed, hadm_id-filtered)
# ---------------------------------------------------------------------------


def _read_csv_lower(path: str) -> pd.DataFrame:
    """Read a CSV/CSV.gz/parquet via DuckDB and lowercase its columns."""
    with duckdb.connect() as conn:
        if path.endswith(".parquet"):
            df = conn.execute("SELECT * FROM parquet_scan(?)", [path]).fetchdf()
        else:
            df = conn.execute("SELECT * FROM read_csv_auto(?)", [path]).fetchdf()
    df.columns = df.columns.str.lower()
    return df


def load_admissions(path: str, hadm_ids: set[int]) -> pd.DataFrame:
    """Load ADMISSIONS rows for the given hadm_ids.

    Returns a DataFrame indexed by hadm_id with ``hospital_expire_flag`` and
    ``los_days`` columns. Rows whose hadm_id is not in ``hadm_ids`` are
    dropped. ``los_days`` mirrors the cohort doc's convention:
    ``DATE_DIFF('day', ADMITTIME, DISCHTIME)`` — the integer number of day
    boundaries crossed between admit and discharge, not fractional days.
    Using fractional days would put admissions like ``Jan 1 23:00 -> Jan 4
    01:00`` (50 hours, two boundaries crossed) on the wrong side of the
    ``LOS >= 3`` cohort filter.
    """
    df = _read_csv_lower(path)
    df = df[df["hadm_id"].isin(hadm_ids)].copy()
    if len(df) < len(hadm_ids):
        # By construction every hadm_id in NOTEEVENTS has a row in ADMISSIONS,
        # so a shortfall here signals stale or mismatched MIMIC tables. Warn
        # rather than raise: LOS and mortality are still meaningful over the
        # rows that did match, just with a smaller denominator.
        missing = len(hadm_ids) - len(df)
        warnings.warn(
            f"{missing} hadm_id(s) from the extraction universe are missing "
            f"from {path}. LOS and mortality denominators will be smaller "
            f"than the successful-extraction count.",
            stacklevel=2,
        )
    admit_date = pd.to_datetime(df["admittime"]).dt.normalize()
    disch_date = pd.to_datetime(df["dischtime"]).dt.normalize()
    df["los_days"] = (disch_date - admit_date).dt.days
    df["hospital_expire_flag"] = df["hospital_expire_flag"].astype(int)
    df = df.set_index("hadm_id")
    return df[["hospital_expire_flag", "los_days"]]


def load_note_category_availability(path: str, hadm_ids: set[int]) -> pd.DataFrame:
    """Classify each hadm_id as clinical_narrative or imaging_only.

    Uses a single DuckDB aggregation over NOTEEVENTS to avoid loading the
    full ~2M-row table into pandas. Returns a DataFrame indexed by hadm_id
    with one column ``note_category`` whose values are
    ``"clinical_narrative"`` or ``"imaging_only"``.
    """
    query = """
        SELECT
            HADM_ID AS hadm_id,
            BOOL_OR(
                CATEGORY IN ('Nursing', 'Nursing/other')
                OR STARTS_WITH(CATEGORY, 'Physician')
            ) AS has_clinical_narrative
        FROM read_csv_auto(?)
        WHERE HADM_ID IS NOT NULL
        GROUP BY HADM_ID
    """
    with duckdb.connect() as conn:
        df = conn.execute(query, [path]).fetchdf()
    df.columns = df.columns.str.lower()
    df = df[df["hadm_id"].isin(hadm_ids)].copy()
    df["note_category"] = df["has_clinical_narrative"].map(
        {True: CLINICAL_NARRATIVE, False: IMAGING_ONLY}
    )
    df["hadm_id"] = df["hadm_id"].astype(int)
    return df.set_index("hadm_id")[["note_category"]]


def load_per_admission_note_volume(path: str, hadm_ids: set[int]) -> pd.DataFrame:
    """Per-admission note count and total characters, excluding discharge summaries.

    The discharge summary is excluded because the BHC is part of it: it
    is the generation target, not source content. Admissions whose only
    note is the discharge summary do not appear in the returned frame.
    Callers should reindex against their full universe and fill missing
    rows with zeros.
    """
    query = """
        SELECT
            HADM_ID AS hadm_id,
            COUNT(*) AS note_count,
            SUM(LENGTH(TEXT)) AS note_chars
        FROM read_csv_auto(?)
        WHERE HADM_ID IS NOT NULL
          AND CATEGORY != 'Discharge summary'
        GROUP BY HADM_ID
    """
    with duckdb.connect() as conn:
        df = conn.execute(query, [path]).fetchdf()
    df.columns = df.columns.str.lower()
    df = df[df["hadm_id"].isin(hadm_ids)].copy()
    df["hadm_id"] = df["hadm_id"].astype(int)
    df["note_count"] = df["note_count"].astype(int)
    df["note_chars"] = df["note_chars"].fillna(0).astype(int)
    return df.set_index("hadm_id")[["note_count", "note_chars"]]


def load_icu_dbsource(path: str, hadm_ids: set[int]) -> pd.DataFrame:
    """Classify each hadm_id by ICU dbsource (carevue / metavision / mixed).

    Admissions without any ICU stay are not present in the returned frame;
    callers that want to surface them should reindex by their full hadm_id
    universe and fill the missing rows with ``DBSOURCE_NO_ICU``.
    """
    # COALESCE guards against all-NULL DBSOURCE groups, where BOOL_AND would
    # otherwise return NULL and propagate as pandas NA into the bucketing.
    query = """
        SELECT
            HADM_ID AS hadm_id,
            COALESCE(BOOL_AND(LOWER(DBSOURCE) = 'metavision'), FALSE) AS all_metavision,
            COALESCE(BOOL_AND(LOWER(DBSOURCE) = 'carevue'), FALSE) AS all_carevue
        FROM read_csv_auto(?)
        WHERE HADM_ID IS NOT NULL
        GROUP BY HADM_ID
    """
    with duckdb.connect() as conn:
        df = conn.execute(query, [path]).fetchdf()
    df.columns = df.columns.str.lower()
    df = df[df["hadm_id"].isin(hadm_ids)].copy()
    df["dbsource"] = DBSOURCE_MIXED
    df.loc[df["all_carevue"], "dbsource"] = DBSOURCE_CAREVUE
    df.loc[df["all_metavision"], "dbsource"] = DBSOURCE_METAVISION
    df["hadm_id"] = df["hadm_id"].astype(int)
    return df.set_index("hadm_id")[["dbsource"]]


# ---------------------------------------------------------------------------
# Population stats assembly
# ---------------------------------------------------------------------------


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear interpolation percentile (pandas default)."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    pos = q * (n - 1)
    low = int(pos)
    high = min(low + 1, n - 1)
    frac = pos - low
    return sorted_values[low] + frac * (sorted_values[high] - sorted_values[low])


def _build_admission_frame(
    results: Sequence[BHCResult],
    admissions: pd.DataFrame,
    note_categories: pd.DataFrame,
    note_volume: pd.DataFrame,
    icu_dbsource: pd.DataFrame | None,
) -> pd.DataFrame:
    """One DataFrame keyed by hadm_id with all attributes the stats need."""
    rows = [
        {
            "hadm_id": r.hadm_id,
            "subject_id": r.subject_id,
            "bhc_format": r.bhc_structure.category if r.bhc_structure else None,
            "bhc_char_count": r.bhc_char_count,
        }
        for r in results
        if r.extraction_success and r.hadm_id is not None
    ]
    if rows:
        base = pd.DataFrame(rows).set_index("hadm_id")
    else:
        # Empty universe: build the empty frame explicitly so set_index does
        # not fail on a column-less DataFrame.
        base = pd.DataFrame(
            columns=["subject_id", "bhc_format", "bhc_char_count"],
            index=pd.Index([], name="hadm_id", dtype="int64"),
        )
    df = base.join(admissions, how="left").join(note_categories, how="left")
    df = df.join(note_volume, how="left")
    # Admissions whose only note is the discharge summary land in the
    # universe with no row in note_volume; treat them as zero, not NaN.
    df["note_count"] = df["note_count"].fillna(0).astype(int)
    df["note_chars"] = df["note_chars"].fillna(0).astype(int)
    if icu_dbsource is not None:
        df = df.join(icu_dbsource, how="left")
        df["dbsource"] = df["dbsource"].fillna(DBSOURCE_NO_ICU)
    return df


def _system_slice(df: pd.DataFrame, dbsource: str | None) -> pd.DataFrame:
    if dbsource is None:
        return df
    return df[df["dbsource"] == dbsource]


def _bucket_cell(sub: pd.DataFrame) -> BucketSystemCounts:
    cn = int((sub["note_category"] == CLINICAL_NARRATIVE).sum())
    imaging = int((sub["note_category"] == IMAGING_ONLY).sum())
    return {"clinical_narrative": cn, "imaging_only": imaging}


def _bucketed_availability(
    df: pd.DataFrame,
    *,
    value_col: str,
    buckets: Sequence[tuple[str, int, int | None]],
) -> BucketedAvailability:
    """Bucket ``df`` rows by ``value_col`` and count note-category per system.

    Caller must restrict ``df`` to rows with a known ``note_category`` and
    include a ``dbsource`` column. Bucket bounds are inclusive on both
    ends. An open-ended bucket (``high is None``) matches ``v >= low``.
    """
    valid = df.dropna(subset=[value_col])
    rows: list[BucketRow] = []
    for label, low, high in buckets:
        if high is None:
            mask = valid[value_col] >= low
        else:
            mask = (valid[value_col] >= low) & (valid[value_col] <= high)
        bucket_slice = valid[mask]
        row: BucketRow = {
            "label": label,
            "total": _bucket_cell(_system_slice(bucket_slice, None)),
            "carevue": _bucket_cell(_system_slice(bucket_slice, DBSOURCE_CAREVUE)),
            "metavision": _bucket_cell(_system_slice(bucket_slice, DBSOURCE_METAVISION)),
        }
        rows.append(row)
    excluded: dict[str, int] = {
        column: int(len(_system_slice(df, dbsource)) - len(_system_slice(valid, dbsource)))
        for column, dbsource in _SYSTEM_COLUMNS
    }
    return {"rows": rows, "excluded": excluded}


def _note_category_stats(df: pd.DataFrame, *, with_dbsource: bool) -> NoteCategoryAvailability:
    valid = df.dropna(subset=["note_category"])
    counts = valid["note_category"].value_counts().to_dict()
    counts.setdefault(CLINICAL_NARRATIVE, 0)
    counts.setdefault(IMAGING_ONLY, 0)
    total = int(counts[CLINICAL_NARRATIVE] + counts[IMAGING_ONLY])

    by_dbsource: dict[str, dict[str, int]] | None = None
    by_los_bucket: BucketedAvailability | None = None
    by_bhc_length_bucket: BucketedAvailability | None = None
    if with_dbsource and "dbsource" in valid.columns:
        by_dbsource = {}
        for source, sub in valid.groupby("dbsource"):
            sub_counts = sub["note_category"].value_counts().to_dict()
            sub_counts.setdefault(CLINICAL_NARRATIVE, 0)
            sub_counts.setdefault(IMAGING_ONLY, 0)
            by_dbsource[str(source)] = {
                CLINICAL_NARRATIVE: int(sub_counts[CLINICAL_NARRATIVE]),
                IMAGING_ONLY: int(sub_counts[IMAGING_ONLY]),
            }

        by_los_bucket = _bucketed_availability(valid, value_col="los_days", buckets=_LOS_BUCKETS)
        by_bhc_length_bucket = _bucketed_availability(
            valid, value_col="bhc_char_count", buckets=_BHC_LENGTH_BUCKETS
        )

    return {
        "counts": {
            CLINICAL_NARRATIVE: int(counts[CLINICAL_NARRATIVE]),
            IMAGING_ONLY: int(counts[IMAGING_ONLY]),
        },
        "total": total,
        "by_dbsource": by_dbsource,
        "by_los_bucket": by_los_bucket,
        "by_bhc_length_bucket": by_bhc_length_bucket,
    }


def _los_stats(df: pd.DataFrame) -> LosStats:
    los = df["los_days"].dropna().astype(float).tolist()
    los.sort()
    if not los:
        return {
            "mean": 0.0,
            "median": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "p90": 0.0,
            "lt_3_days": 0,
            "total": 0,
        }
    return {
        "mean": sum(los) / len(los),
        "median": _percentile(los, 0.5),
        "p25": _percentile(los, 0.25),
        "p75": _percentile(los, 0.75),
        "p90": _percentile(los, 0.9),
        "lt_3_days": sum(1 for v in los if v < _LOS_FILTER_DAYS),
        "total": len(los),
    }


def _format_cross_tab(df: pd.DataFrame) -> FormatByNoteCategory:
    """2x2 cross-tab of BHC format (narrative, domain_organized) by note-category.

    Templates (standardized_template) are excluded per the cohort doc.
    """
    keep = df[df["bhc_format"].isin(["narrative", "domain_organized"])]
    keep = keep.dropna(subset=["note_category"])

    cells: dict[str, dict[str, int]] = {}
    for fmt in ("narrative", "domain_organized"):
        cells[fmt] = {CLINICAL_NARRATIVE: 0, IMAGING_ONLY: 0}

    grouped = keep.groupby(["bhc_format", "note_category"]).size()
    for (fmt, cat), n in grouped.items():
        if fmt in cells and cat in cells[fmt]:
            cells[fmt][cat] = int(n)

    row_totals = {fmt: sum(cells[fmt].values()) for fmt in cells}
    col_totals = {
        CLINICAL_NARRATIVE: sum(cells[f][CLINICAL_NARRATIVE] for f in cells),
        IMAGING_ONLY: sum(cells[f][IMAGING_ONLY] for f in cells),
    }
    grand_total = sum(row_totals.values())
    return {
        "cells": cells,
        "row_totals": row_totals,
        "col_totals": col_totals,
        "grand_total": grand_total,
    }


def _mortality_stats(df: pd.DataFrame) -> MortalityStats:
    valid = df["hospital_expire_flag"].dropna().astype(int)
    return {"deaths": int(valid.sum()), "total": len(valid)}


def _distribution(values: pd.Series) -> DistributionStats:
    sorted_vals = sorted(values.dropna().tolist())
    n = len(sorted_vals)
    if n == 0:
        return {
            "median": 0.0,
            "mean": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "p90": 0.0,
            "min": 0,
            "max": 0,
            "n": 0,
        }
    return {
        "median": _percentile(sorted_vals, 0.5),
        "mean": sum(sorted_vals) / n,
        "p25": _percentile(sorted_vals, 0.25),
        "p75": _percentile(sorted_vals, 0.75),
        "p90": _percentile(sorted_vals, 0.9),
        "min": int(sorted_vals[0]),
        "max": int(sorted_vals[-1]),
        "n": n,
    }


def _note_volume_stats(df: pd.DataFrame, *, with_dbsource: bool) -> NoteVolumeStats:
    valid = df.dropna(subset=["note_category"])
    columns = _SYSTEM_COLUMNS if with_dbsource else ((BUCKET_COLUMN_TOTAL, None),)
    note_count: dict[str, dict[str, DistributionStats]] = {}
    note_chars: dict[str, dict[str, DistributionStats]] = {}
    for system_key, dbsource in columns:
        note_count[system_key] = {}
        note_chars[system_key] = {}
        for class_key in (CLINICAL_NARRATIVE, IMAGING_ONLY):
            sub = _system_slice(valid, dbsource)
            sub = sub[sub["note_category"] == class_key]
            note_count[system_key][class_key] = _distribution(sub["note_count"])
            note_chars[system_key][class_key] = _distribution(sub["note_chars"])
    return {"note_count": note_count, "note_chars": note_chars}


def compute_population_stats(
    results: Sequence[BHCResult],
    *,
    admissions_path: str,
    noteevents_path: str,
    icustays_path: str | None = None,
) -> PopulationStats:
    """Compute the four MIMIC-derived population statistics.

    The admission universe is the set of hadm_ids whose extraction succeeded.
    All MIMIC tables are filtered to that set before computation. The
    ``format_by_note_category`` cross-tab additionally drops admissions whose
    BHC was classified as ``standardized_template``.

    Self-check: the cross-tab row totals are validated against the structure
    distribution counts (narrative + domain_organized) over the same universe.
    A mismatch raises ``ValueError`` so the caller fails loudly.
    """
    successful = [r for r in results if r.extraction_success and r.hadm_id is not None]
    hadm_ids = {int(r.hadm_id) for r in successful if r.hadm_id is not None}

    admissions = load_admissions(admissions_path, hadm_ids)
    note_categories = load_note_category_availability(noteevents_path, hadm_ids)
    note_volume = load_per_admission_note_volume(noteevents_path, hadm_ids)
    icu_dbsource = load_icu_dbsource(icustays_path, hadm_ids) if icustays_path else None

    df = _build_admission_frame(successful, admissions, note_categories, note_volume, icu_dbsource)
    cross_tab = _format_cross_tab(df)

    # Self-check: cross-tab row totals must equal the structure-distribution
    # counts over the same universe. A successful BHC extraction comes from a
    # NOTEEVENTS row by definition, so any disagreement signals a data
    # integrity bug (e.g. NOTEEVENTS path passed in is from a different
    # MIMIC version, or the extraction JSONL is stale).
    expected_narrative = sum(
        1 for r in successful if r.bhc_structure and r.bhc_structure.category == "narrative"
    )
    expected_domain = sum(
        1 for r in successful if r.bhc_structure and r.bhc_structure.category == "domain_organized"
    )
    actual_narrative = cross_tab["row_totals"]["narrative"]
    actual_domain = cross_tab["row_totals"]["domain_organized"]
    if actual_narrative != expected_narrative or actual_domain != expected_domain:
        msg = (
            "Cross-tab row totals do not match structure-distribution counts "
            "over the same universe. The NOTEEVENTS file may not match the "
            "extraction JSONL. "
            f"narrative: cross-tab {actual_narrative}, structure {expected_narrative}; "
            f"domain_organized: cross-tab {actual_domain}, structure {expected_domain}."
        )
        raise ValueError(msg)

    return {
        "note_category_availability": _note_category_stats(
            df, with_dbsource=icu_dbsource is not None
        ),
        "los_distribution": _los_stats(df),
        "format_by_note_category": cross_tab,
        "mortality": _mortality_stats(df),
        "note_volume": _note_volume_stats(df, with_dbsource=icu_dbsource is not None),
    }


# ---------------------------------------------------------------------------
# MIMIC directory auto-discovery
# ---------------------------------------------------------------------------


def discover_mimic3_paths(mimic_dir: str | Path) -> dict[str, str | None]:
    """Locate ADMISSIONS, NOTEEVENTS, and ICUSTAYS inside a MIMIC-III root dir.

    Looks for both gzipped and plain CSV variants. Missing files map to None.
    Used by the CLI to translate ``--mimic-dir`` into the individual paths
    that ``compute_population_stats`` requires.
    """
    base = Path(mimic_dir)
    found: dict[str, str | None] = {}
    for key, name in (
        ("admissions", "ADMISSIONS"),
        ("noteevents", "NOTEEVENTS"),
        ("icustays", "ICUSTAYS"),
    ):
        for ext in (".csv.gz", ".csv", ".parquet"):
            candidate = base / f"{name}{ext}"
            if candidate.exists():
                found[key] = str(candidate)
                break
        else:
            found[key] = None
    return found
