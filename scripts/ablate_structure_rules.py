"""Ablate the structure-detection rules in structure.py.

Question: is any component inert, meaning its removal changes no
classification? Inert components can be deleted.

Removes one component at a time and reports how many admissions change
classification. Zero means inert. The rule comments in structure.py cite this
script, so rerun it after editing either rule.

- _count_labeled_sections, where a component is one whole pattern. The patterns
  feed a count compared against a threshold, so removing one reclassifies
  admissions as narrative rather than only losing a match.
- _ACUTE_CHRONIC_RE, where a component is a sub-expression of the single
  pattern. Removing one loses matches. Removing an anchor adds them, so the
  anchors are reported as matches admitted.

Reads the extraction JSONL for both datasets from --results-dir.

Usage:
    uv run python scripts/ablate_structure_rules.py \
        --results-dir ../phd-dissertation-experiment-results/bhc_extraction
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from mimic_bhc import structure

DATASETS = ("mimic-iii", "mimic-iv")

# An admission is eligible when the extractor found a BHC of more than this
# many characters. Every script under scripts/ that reads this pool carries a
# copy. Change them together.
_MIN_BHC_CHARS = 500

# Components of _ACUTE_CHRONIC_RE, assembled so one can be swapped out at a time.
PREFIX = r"^[ \t]*(?:[>*#]+[ \t]*)?"
KEYS = r"(?:acute|active|chronic|stable|inactive)"
COMPOUND = r"[/ \t]*(?:active|stable|chronic)?"
NOUN = r"[ \t]*(?:issues?|problems?)[ \t]*:?"
TAIL = r"[ \t]*[*=]*[ \t]*$"


def _acute_chronic(keys=KEYS, compound=COMPOUND, noun=NOUN, tail=TAIL, prefix=PREFIX):
    """Build an _ACUTE_CHRONIC_RE variant with one or more components replaced."""
    return re.compile(prefix + keys + compound + noun + tail, re.IGNORECASE | re.MULTILINE)


AC_VARIANTS = {
    "prefix dividers [>*#]": _acute_chronic(prefix=r"^[ \t]*"),
    "keyword acute": _acute_chronic(keys=r"(?:active|chronic|stable|inactive)"),
    "keyword active": _acute_chronic(keys=r"(?:acute|chronic|stable|inactive)"),
    "keyword chronic": _acute_chronic(keys=r"(?:acute|active|stable|inactive)"),
    "keyword stable": _acute_chronic(keys=r"(?:acute|active|chronic|inactive)"),
    "keyword inactive": _acute_chronic(keys=r"(?:acute|active|chronic|stable)"),
    "compound 2nd keyword": _acute_chronic(compound=""),
    "noun issues": _acute_chronic(noun=r"[ \t]*problems?[ \t]*:?"),
    "noun problems": _acute_chronic(noun=r"[ \t]*issues?[ \t]*:?"),
    "optional plural s?": _acute_chronic(noun=r"[ \t]*(?:issues|problems)[ \t]*:?"),
    "optional colon :?": _acute_chronic(noun=r"[ \t]*(?:issues?|problems?)[ \t]*:"),
    "trailing [*=]": _acute_chronic(tail=r"[ \t]*$"),
}
AC_ANCHORS = {
    "end anchor $": _acute_chronic(tail=""),
    "start anchor ^": _acute_chronic(prefix=r"[ \t]*(?:[>*#]+[ \t]*)?"),
}
AC_FULL = _acute_chronic()

SECTION_PATTERNS = {
    "hash entries (#)": structure._HASH_ENTRY_RE,
    "numbered entries": structure._NUMBERED_ENTRY_RE,
    "asterisk entries (*)": structure._ASTERISK_ENTRY_RE,
}
THRESHOLD = structure._MIN_DOMAIN_SECTIONS


def count_sections(text, *, skip=None, skip_standalone=False, discharge_filter=True):
    """Reimplement _count_labeled_sections with one pattern optionally removed."""
    starts = set()
    for name, pattern in SECTION_PATTERNS.items():
        if name != skip:
            starts.update(m.start() for m in pattern.finditer(text))
    if not skip_standalone:
        starts.update(
            m.start()
            for m in structure._STANDALONE_HEADER_RE.finditer(text)
            if not (discharge_filter and structure._is_discharge_header(text, m.start()))
        )
    return len(starts)


def iter_bhcs(results_dir):
    """Yield BHC text for every admission in the eligible pool."""
    for dataset in DATASETS:
        with (results_dir / f"bhc_extraction_{dataset}.jsonl").open() as f:
            for line in f:
                record = json.loads(line)
                eligible = (
                    record["extraction_success"]
                    and (record["bhc_char_count"] or 0) > _MIN_BHC_CHARS
                )
                if eligible:
                    yield record["bhc_text"] or ""


def tally(results_dir):
    """Count how each component's removal changes classification."""
    counts = {
        "total": 0,
        "flagged_ac": 0,
        "domain_organized": 0,
        "discharge_filter_saves": 0,
    }
    ac_lost = dict.fromkeys(AC_VARIANTS, 0)
    ac_gained = dict.fromkeys(AC_ANCHORS, 0)
    section_lost = dict.fromkeys([*SECTION_PATTERNS, "standalone headers"], 0)

    for text in iter_bhcs(results_dir):
        counts["total"] += 1

        if AC_FULL.search(text) is not None:
            counts["flagged_ac"] += 1
            for name, pattern in AC_VARIANTS.items():
                if pattern.search(text) is None:
                    ac_lost[name] += 1
        else:
            for name, pattern in AC_ANCHORS.items():
                if pattern.search(text) is not None:
                    ac_gained[name] += 1

        if count_sections(text) >= THRESHOLD:
            counts["domain_organized"] += 1
            for name in SECTION_PATTERNS:
                if count_sections(text, skip=name) < THRESHOLD:
                    section_lost[name] += 1
            if count_sections(text, skip_standalone=True) < THRESHOLD:
                section_lost["standalone headers"] += 1
        elif count_sections(text, discharge_filter=False) >= THRESHOLD:
            counts["discharge_filter_saves"] += 1

    return counts, ac_lost, ac_gained, section_lost


def report(counts, ac_lost, ac_gained, section_lost):
    """Print both ablation tables."""
    domain = counts["domain_organized"]
    flagged = counts["flagged_ac"]
    print(f"eligible BHCs across {', '.join(DATASETS)}: {counts['total']:,}\n")

    print(f"_count_labeled_sections, threshold {THRESHOLD}")
    print(f"  classified domain-organized: {domain:,}")
    for name, lost in sorted(section_lost.items(), key=lambda kv: -kv[1]):
        print(f"  drop {name:<24} {lost:>9,} become narrative  {lost / domain:>7.2%}")
    saved = counts["discharge_filter_saves"]
    print(f"  discharge-header exclusion    {saved:>9,} kept out of domain-organized\n")

    print("_ACUTE_CHRONIC_RE")
    print(f"  flagged: {flagged:,}")
    for name, lost in sorted(ac_lost.items(), key=lambda kv: -kv[1]):
        print(f"  drop {name:<24} {lost:>9,} unflagged         {lost / flagged:>7.2%}")
    for name, gained in sorted(ac_gained.items(), key=lambda kv: -kv[1]):
        print(f"  drop {name:<24} {gained:>9,} newly admitted")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--results-dir",
        required=True,
        type=Path,
        help="Directory holding bhc_extraction_*.jsonl and bhc_structure_*.csv",
    )
    args = parser.parse_args()
    report(*tally(args.results_dir.expanduser()))


if __name__ == "__main__":
    main()
