"""Summary statistics for BHC extraction results."""

from __future__ import annotations

from collections import Counter
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


def compute_bhc_stats(results: Sequence[BHCResult]) -> BHCStats:
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

    successful = [r for r in results if r.extraction_success]

    # Failure breakdown
    failure_reasons = dict(
        Counter(r.failure_reason for r in results if not r.extraction_success and r.failure_reason)
    )

    # Header distribution
    header_dist = dict(Counter(r.start_header for r in successful if r.start_header))

    # End header distribution
    end_dist = dict(Counter(r.end_header for r in successful if r.end_header is not None))
    unbounded = sum(1 for r in successful if r.end_header is None)

    # Structure distribution
    format_dist = dict(Counter(r.bhc_structure.category for r in successful if r.bhc_structure))

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
        "failures": total - len(successful),
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
