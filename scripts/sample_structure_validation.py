r"""Draw the validation sample for the BHC format coding rules.

Question: does a person applying the written coding rules reach the same labels
as structure.py? This script draws the cases that question is answered on.

The draw is stratified into three groups, narrative and domain-organized for
the category rule and acute/chronic for the acuity rule, which fires on 2% of
MIMIC-III.

The coder works from the text alone. Cases are shuffled and numbered. Each
group splits as evenly as its size allows across the two datasets.

Reads the extraction JSONL for both datasets from --results-dir. Writes the
coding sheet and the key to --out-dir. Both contain MIMIC data and belong outside
this repository.

How to use it:

1. Extract both datasets into --results-dir, if they are not there already.
   The draw takes half its cases from each, so both are required. Skip
   --include-note-text, which quadruples the file and adds nothing this
   script reads:

       export MIMIC3_NOTES=../mimic-iii/physionet.org/files/mimiciii/1.4/
       export MIMIC4_NOTES=../mimic-iv-note/physionet.org/files/mimic-iv-note/2.2/note/
       export RESULTS_DIR=../bhc-results/bhc_extraction

       mimic-bhc-extract extract --input $MIMIC3_NOTES/NOTEEVENTS.csv.gz \
           --output $RESULTS_DIR/bhc_extraction_mimic-iii.jsonl
       mimic-bhc-extract extract --input $MIMIC4_NOTES/discharge.csv.gz \
           --output $RESULTS_DIR/bhc_extraction_mimic-iv.jsonl

2. Run the command below. It writes validation_coding_sheet.md and
   validation_key.md to --out-dir.
3. Open validation_coding_sheet.md and code the cases.
4. Open validation_key.md and count the disagreements per rule. Disagreements
   are the result: score them against MAX_DISAGREEMENTS below. Do not
   reconcile them. A rule over its limit is the thing that is wrong. Revise it
   and re-validate on a fresh draw.

Point --exclude at any file listing hadm_ids that have already been coded or
annotated. On one of those the coder already knows the label, so applying the
rules would test nothing.

Usage:
    uv run python scripts/sample_structure_validation.py \
        --results-dir ../bhc-results/bhc_extraction \
        --exclude previously-annotated-admissions.md \
        --seed 20260802 \
        --out-dir ../bhc-results/bhc_structure_validation
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from mimic_bhc.stats import is_structure_eligible
from mimic_bhc.structure import (
    CATEGORY_NARRATIVE,
    MODIFIER_ACUTE_CHRONIC,
    classify_bhc_structure,
)

DATASETS = ("mimic-iii", "mimic-iv")

GROUP_NARRATIVE = "narrative"
GROUP_DOMAIN = "domain_organized"
GROUP_ACUTE_CHRONIC = "acute_chronic"

# The category rule is scored over its two groups pooled, at 30 cases, because
# 30 with no disagreement puts a lower bound near 90% by the rule of three.
# Acute/chronic is a single pattern over header lines, so a correct rule should
# disagree on none of its 10 cases. Two disagreements would be strong
# evidence that the pattern itself is wrong.
QUOTAS = {
    (GROUP_NARRATIVE, "mimic-iii"): 8,
    (GROUP_NARRATIVE, "mimic-iv"): 7,
    (GROUP_DOMAIN, "mimic-iii"): 7,
    (GROUP_DOMAIN, "mimic-iv"): 8,
    (GROUP_ACUTE_CHRONIC, "mimic-iii"): 5,
    (GROUP_ACUTE_CHRONIC, "mimic-iv"): 5,
}

# Scored per rule, with the groups pooled: a percentage over 10 cases would
# turn one disagreement into a 10-point swing.
MAX_DISAGREEMENTS = {
    "category": 3,  # over the 30 narrative and domain-organized cases pooled
    "acute_chronic": 1,  # over the 10 acute/chronic cases
}

# hadm_ids are 6 digits in MIMIC-III and 8 in MIMIC-IV.
_HADM_ID_RE = re.compile(r"\b(?:\d{6}|\d{8})\b")


@dataclass(frozen=True)
class Case:
    """One drawn admission, in the shuffled order the coder sees."""

    case_no: int
    hadm_id: str
    dataset: str
    group: str


def group_for(result):
    """Return the group an admission belongs to, or None if no group covers it.

    A standardized template counts as domain-organized, which is what the
    coding rules tell the coder to assign. Narrative with an acute/chronic
    header gets no group: it occurs, but one such case could disagree on the
    category and on the acuity at once, leaving neither count meaningful.
    """
    if MODIFIER_ACUTE_CHRONIC in result.modifiers:
        if result.category == CATEGORY_NARRATIVE:
            return None
        return GROUP_ACUTE_CHRONIC
    if result.category == CATEGORY_NARRATIVE:
        return GROUP_NARRATIVE
    return GROUP_DOMAIN


def read_exclusions(paths):
    """Collect hadm_ids to keep out of the draw, reporting the count per file."""
    excluded = set()
    for path in paths:
        found = set(_HADM_ID_RE.findall(path.expanduser().read_text()))
        print(f"  {len(found):>4} hadm_ids excluded from {path.name}")
        excluded |= found
    return excluded


def iter_records(results_dir):
    """Yield (dataset, hadm_id, bhc_text) for every admission in the eligible pool."""
    for dataset in DATASETS:
        with (results_dir / f"bhc_extraction_{dataset}.jsonl").open() as f:
            for line in f:
                record = json.loads(line)
                if is_structure_eligible(record):
                    yield dataset, str(record["hadm_id"]), record["bhc_text"] or ""


def build_pools(records, exclude):
    """Group eligible admissions into the groups they are candidates for."""
    pools = defaultdict(list)
    for dataset, hadm_id, text in records:
        if hadm_id in exclude:
            continue
        group = group_for(classify_bhc_structure(text))
        if (group, dataset) in QUOTAS:
            pools[group, dataset].append(hadm_id)
    return {key: sorted(ids) for key, ids in pools.items()}


def draw(pools, seed):
    """Sample each group to its quota, then shuffle and number the whole draw."""
    rng = random.Random(seed)
    drawn = []
    for (group, dataset), quota in sorted(QUOTAS.items()):
        sampled = rng.sample(pools[group, dataset], quota)
        drawn += [(hadm_id, dataset, group) for hadm_id in sampled]
    rng.shuffle(drawn)
    return [Case(i, *entry) for i, entry in enumerate(drawn, start=1)]


def _fence(text):
    """Return a code fence long enough to survive backticks inside the BHC."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def write_coding_sheet(cases, texts, path):
    """Write the coding sheet: the BHC text and blank fields, one block per case."""
    lines = [
        "# BHC format validation: coding sheet",
        "",
        "Code each case against docs/bhc-format-coding-rules.md in the",
        "mimic-bhc-extraction repository. It gives the rules, the four fields",
        "to record, and what to do with a case the rules do not settle.",
        "",
    ]
    for case in cases:
        text = texts[case.hadm_id]
        fence = _fence(text)
        lines += [
            f"## Case {case.case_no}",
            "",
            f"{fence}text",
            text.rstrip("\n"),
            fence,
            "",
            "- Category (narrative / domain-organized):",
            "- Acute/chronic partition (yes / no):",
            "- Transitional issues section (yes / no):",
            "- Note, only if the rules did not settle it:",
            "",
        ]
    path.write_text("\n".join(lines))


def write_key(cases, path, seed):
    """Write the group structure.py assigned to each case, as a table beside the sheet."""
    lines = [
        "# BHC format validation: key",
        "",
        f"Drawn with seed {seed}. Open once the coding sheet is filled in.",
        "",
        "| Case | hadm_id | Dataset | Group |",
        "|---:|---:|---|---|",
    ]
    lines += [
        f"| {case.case_no} | {case.hadm_id} | {case.dataset} | {case.group} |" for case in cases
    ]
    path.write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--results-dir",
        required=True,
        type=Path,
        help=(
            "Directory containing bhc_extraction_mimic-iii.jsonl and bhc_extraction_mimic-iv.jsonl"
        ),
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        type=Path,
        help="File listing hadm_ids to keep out of the draw. Repeatable.",
    )
    parser.add_argument(
        "--seed",
        required=True,
        type=int,
        help="Any integer. Recorded in the key so the draw can be repeated.",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        type=Path,
        help="Where to write the coding sheet and the key. Holds BHC text.",
    )
    args = parser.parse_args()

    print("exclusions")
    exclude = read_exclusions(args.exclude)

    results_dir = args.results_dir.expanduser()
    out_dir = args.out_dir.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    print("\nbuilding candidate pools...")
    pools = build_pools(iter_records(results_dir), exclude)
    print("\ncandidate pools")
    for key, quota in sorted(QUOTAS.items()):
        print(f"  {key[0]:<17} {key[1]:<10} {len(pools.get(key, [])):>7,} candidates, draw {quota}")

    cases = draw(pools, args.seed)
    wanted = {case.hadm_id for case in cases}
    texts = {hadm_id: text for _, hadm_id, text in iter_records(results_dir) if hadm_id in wanted}

    sheet = out_dir / "validation_coding_sheet.md"
    key = out_dir / "validation_key.md"
    write_coding_sheet(cases, texts, sheet)
    write_key(cases, key, args.seed)
    print(f"\nseed {args.seed}, {len(cases)} cases")
    print(f"  sheet {sheet}")
    print(f"  key   {key}")


if __name__ == "__main__":
    main()
