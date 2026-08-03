r"""Report the corpus figures for BHC organizational format.

How the two formats split, how long each runs, and how often the acute/chronic
partition appears, computed from the extraction JSONL.

Standardized templates fold into domain-organized, which is how the coding
rules tell a coder to label them. The subtype is also reported on its own line.

Takes one extraction JSONL, so run it once per dataset. The dataset name is
read off the hadm_id, 6 digits in MIMIC-III and 8 in MIMIC-IV, so the file can
be called anything.

Usage:
    uv run python scripts/bhc_format_stats.py --input bhc.jsonl
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from mimic_bhc.structure import (
    CATEGORY_NARRATIVE,
    CATEGORY_STANDARDIZED_TEMPLATE,
    MODIFIER_ACUTE_CHRONIC,
    classify_bhc_structure,
)

# An admission is eligible when the extractor found a BHC of more than this
# many characters. Every script under scripts/ that reads this pool carries a
# copy. Change them together.
_MIN_BHC_CHARS = 500


@dataclass(frozen=True)
class Summary:
    """One dataset's figures. Templates are counted in `domain` and in `template`."""

    total: int
    narrative: int
    domain: int
    template: int
    acute_chronic: int
    median: int
    narrative_median: int
    domain_median: int


def dataset_for(hadm_id):
    """Name the dataset an admission came from by the width of its hadm_id."""
    return "mimic-iii" if len(str(hadm_id)) == 6 else "mimic-iv"


def summarize(rows):
    """Fold templates into domain-organized, then count the categories and modifiers."""
    narrative_lengths = []
    domain_lengths = []
    template = acute_chronic = 0
    for result, length in rows:
        if result.category == CATEGORY_NARRATIVE:
            narrative_lengths.append(length)
        else:
            domain_lengths.append(length)
            if result.category == CATEGORY_STANDARDIZED_TEMPLATE:
                template += 1
        if MODIFIER_ACUTE_CHRONIC in result.modifiers:
            acute_chronic += 1

    return Summary(
        total=len(narrative_lengths) + len(domain_lengths),
        narrative=len(narrative_lengths),
        domain=len(domain_lengths),
        template=template,
        acute_chronic=acute_chronic,
        median=round(statistics.median(narrative_lengths + domain_lengths)),
        narrative_median=round(statistics.median(narrative_lengths)),
        domain_median=round(statistics.median(domain_lengths)),
    )


def read_rows(path):
    """Group every eligible admission by dataset, classified and measured.

    Also counts the discharge summaries per dataset, eligible or not, which is
    the denominator the eligible pool is reported against. One record is one
    discharge summary, after MIMIC-III's addendum deduplication.
    """
    by_dataset = defaultdict(list)
    summaries = Counter()
    with path.expanduser().open() as f:
        for line in f:
            record = json.loads(line)
            dataset = dataset_for(record["hadm_id"])
            summaries[dataset] += 1
            length = record["bhc_char_count"] or 0
            if not record["extraction_success"] or length <= _MIN_BHC_CHARS:
                continue
            result = classify_bhc_structure(record["bhc_text"] or "")
            by_dataset[dataset].append((result, length))
    return by_dataset, summaries


def report(by_dataset, summaries):
    """Print one block per dataset, with shares to two decimal places."""
    for dataset, rows in sorted(by_dataset.items()):
        s = summarize(rows)
        n = summaries[dataset]
        print(f"\n{dataset}: {s.total:,} eligible BHCs of {n:,} discharge summaries")
        print(f"  narrative              {s.narrative:>8,}  {s.narrative / s.total:>7.2%}")
        print(f"  domain-organized       {s.domain:>8,}  {s.domain / s.total:>7.2%}")
        print(f"    of which templates   {s.template:>8,}  {s.template / s.total:>7.2%}")
        print(f"  acute/chronic          {s.acute_chronic:>8,}  {s.acute_chronic / s.total:>7.2%}")
        print(f"  median chars                   {s.median:>8,}")
        print(f"  median chars, narrative        {s.narrative_median:>8,}")
        print(f"  median chars, domain-organized {s.domain_median:>8,}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Extraction JSONL for one dataset",
    )
    args = parser.parse_args()
    report(*read_rows(args.input))


if __name__ == "__main__":
    main()
