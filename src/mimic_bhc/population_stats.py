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

# ICU dbsource buckets. MIMIC-III ICUSTAYS.dbsource is "carevue" or "metavision".
# Admissions are bucketed by the strict rule used by the cohort filter:
# "metavision" requires every ICU stay to be metavision; "carevue" the same.
# "mixed" covers admissions with stays in both systems; "no_icu" admissions
# without any ICU stay record (rare in MIMIC-III).
DBSOURCE_CAREVUE = "carevue"
DBSOURCE_METAVISION = "metavision"
DBSOURCE_MIXED = "mixed"
DBSOURCE_NO_ICU = "no_icu"

# Patient concentration: 4+ is the upper bucket the cohort doc uses for the
# tail of the admission-count distribution.
_PATIENT_CONCENTRATION_TAIL_BUCKET = 4

# Length of stay: < 3 days is the slice the cohort filter (LOS >= 3) removes.
_LOS_FILTER_DAYS = 3


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


class NoteCategoryAvailability(TypedDict):
    """Counts of admissions by note-category availability class."""

    counts: dict[str, int]
    total: int
    by_dbsource: dict[str, dict[str, int]] | None


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


class PopulationStats(TypedDict):
    """Bundle of MIMIC-derived population statistics."""

    note_category_availability: NoteCategoryAvailability
    los_distribution: LosStats
    format_by_note_category: FormatByNoteCategory
    mortality: MortalityStats


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
    icu_dbsource: pd.DataFrame | None,
) -> pd.DataFrame:
    """One DataFrame keyed by hadm_id with all attributes the stats need."""
    rows = [
        {
            "hadm_id": r.hadm_id,
            "subject_id": r.subject_id,
            "bhc_format": r.bhc_structure.category if r.bhc_structure else None,
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
            columns=["subject_id", "bhc_format"],
            index=pd.Index([], name="hadm_id", dtype="int64"),
        )
    df = base.join(admissions, how="left").join(note_categories, how="left")
    if icu_dbsource is not None:
        df = df.join(icu_dbsource, how="left")
        df["dbsource"] = df["dbsource"].fillna(DBSOURCE_NO_ICU)
    return df


def _note_category_stats(df: pd.DataFrame, *, with_dbsource: bool) -> NoteCategoryAvailability:
    valid = df.dropna(subset=["note_category"])
    counts = valid["note_category"].value_counts().to_dict()
    counts.setdefault(CLINICAL_NARRATIVE, 0)
    counts.setdefault(IMAGING_ONLY, 0)
    total = int(counts[CLINICAL_NARRATIVE] + counts[IMAGING_ONLY])

    by_dbsource: dict[str, dict[str, int]] | None = None
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

    return {
        "counts": {
            CLINICAL_NARRATIVE: int(counts[CLINICAL_NARRATIVE]),
            IMAGING_ONLY: int(counts[IMAGING_ONLY]),
        },
        "total": total,
        "by_dbsource": by_dbsource,
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
    icu_dbsource = load_icu_dbsource(icustays_path, hadm_ids) if icustays_path else None

    df = _build_admission_frame(successful, admissions, note_categories, icu_dbsource)
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
