"""Summary statistics for BHC extraction results."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mimic_bhc.model import BHCResult

# Minimum BHC length (characters) below which an extraction is flagged as
# suspiciously short.  Matches the threshold used in the Stanford
# MIMIC-IV-Ext-BHC dataset (Aali et al., JAMIA 2024).
MIN_BHC_LENGTH = 100


class BHCStats(TypedDict):
    """Return type for ``compute_bhc_stats``."""

    total: int
    successes: int
    failures: int
    success_rate: float
    failure_reasons: dict[str, int]
    header_distribution: dict[str, int]
    end_header_distribution: dict[str, int]
    unbounded_extractions: int
    bhc_length_stats: dict[str, float | int]
    short_extractions: int
    format_distribution: dict[str, int]


def compute_bhc_stats(results: Sequence[BHCResult]) -> BHCStats:  # noqa: C901
    """Compute summary statistics from BHC extraction results."""
    total = len(results)
    if total == 0:
        return {
            "total": 0,
            "successes": 0,
            "failures": 0,
            "success_rate": 0.0,
            "failure_reasons": {},
            "header_distribution": {},
            "end_header_distribution": {},
            "unbounded_extractions": 0,
            "bhc_length_stats": {},
            "short_extractions": 0,
            "format_distribution": {},
        }

    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    # Failure breakdown
    failure_reasons: dict[str, int] = {}
    for r in failed:
        if r.failure_reason:
            failure_reasons[r.failure_reason] = failure_reasons.get(r.failure_reason, 0) + 1

    # Header distribution
    header_dist: dict[str, int] = {}
    for r in successful:
        if r.start_header:
            header_dist[r.start_header] = header_dist.get(r.start_header, 0) + 1

    # End header distribution
    end_dist: dict[str, int] = {}
    unbounded = 0
    for r in successful:
        if r.end_header is not None:
            end_dist[r.end_header] = end_dist.get(r.end_header, 0) + 1
        else:
            unbounded += 1

    # Structure distribution (uses bhc_structure.category when available,
    # falls back to bhc_format for results without structure classification)
    format_dist: dict[str, int] = {}
    for r in successful:
        category = r.bhc_structure.category if r.bhc_structure else r.bhc_format
        if category:
            format_dist[category] = format_dist.get(category, 0) + 1

    # BHC length stats
    length_stats: dict[str, float | int] = {}
    short_extractions = 0
    if successful:
        lengths = sorted(r.bhc_char_count for r in successful)
        short_extractions = sum(1 for ln in lengths if ln < MIN_BHC_LENGTH)
        n = len(lengths)
        length_stats = {
            "mean": sum(lengths) / n,
            "median": _median(lengths),
            "q25": _percentile(lengths, 0.25),
            "q75": _percentile(lengths, 0.75),
            "min": lengths[0],
            "max": lengths[-1],
        }

    return {
        "total": total,
        "successes": len(successful),
        "failures": len(failed),
        "success_rate": len(successful) / total,
        "failure_reasons": failure_reasons,
        "header_distribution": header_dist,
        "end_header_distribution": end_dist,
        "unbounded_extractions": unbounded,
        "bhc_length_stats": length_stats,
        "short_extractions": short_extractions,
        "format_distribution": format_dist,
    }


def _median(sorted_values: list[int]) -> float:
    n = len(sorted_values)
    if n % 2 == 1:
        return float(sorted_values[n // 2])
    return (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2.0


def _percentile(sorted_values: list[int], q: float) -> float:
    """Linear interpolation percentile matching pandas default."""
    n = len(sorted_values)
    pos = q * (n - 1)
    low = int(pos)
    high = min(low + 1, n - 1)
    frac = pos - low
    return sorted_values[low] + frac * (sorted_values[high] - sorted_values[low])
