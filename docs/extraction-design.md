# Extraction Design

How the BHC extractor works: the algorithm, its assumptions, and the decisions behind them.

## BHCResult as the single data structure

`BHCResult` is the canonical representation of a BHC extraction throughout the codebase. Every function that produces, transforms, or serializes extraction data works with `BHCResult` directly rather than building parallel dicts or DataFrames with ad-hoc field names.

`BHCResult` fields are populated in layers. Each layer fills in the fields it owns and passes the result forward using `dataclasses.replace()`:

1. **Extraction layer** (`extract_bhc()` in `mimic_bhc/extract.py`). Takes a note string, nothing else. Regex matching, boundary detection, text slicing. Fills in: `success`, `bhc_text`, `start_header`, `end_header`, `bhc_start`, `bhc_end`, `failure_reason`, `bhc_format`. Patterns live in `mimic_bhc/patterns.py`.

2. **Enrichment layer** (callers like `_process_note()` in `mimic_bhc/pipeline.py`). Adds all context that the extraction layer does not have access to: identifiers (`hadm_id`, `note_row_id`, `subject_id`), note metadata (`chartdate`, `note_length`), and derived fields (`quality_flags`, `bhc_structure` via `classify_bhc_structure()`).

3. **Serialization layer** (`BHCResult.to_dict()` in `mimic_bhc/model.py`). Converts the dataclass to a dict for JSON/JSONL output. This is the only place where field names are mapped to their output representation. No other code constructs output dicts from `BHCResult` fields manually. `BHCResult.from_dict()` reverses the mapping for loading JSON back into objects.

Derived properties like `bhc_char_count` and `bhc_word_count` are computed from `bhc_text` on access (as `@property` methods), not stored as separate fields.

`compute_bhc_stats()` (in `mimic_bhc/stats.py`) takes a sequence of `BHCResult` objects directly, avoiding intermediate DataFrame conversions or field-name translation.

The design rule: if you need a new field on the extraction output, add it to `BHCResult` and populate it in the appropriate layer. Do not create parallel representations.

## Two-phase extraction

`extract_bhc()` operates on a single note string in two phases.

**Phase 1: Header search.** The extractor looks for a BHC section header using regex pattern matching. If no header is found, extraction fails with reason `no_header`.

**Phase 2: End-boundary search.** Starting from the text after the header, the extractor searches for the first line that matches a known end-boundary pattern (a top-level discharge summary section that comes after the BHC). If a boundary is found, the BHC is the text between the header and the boundary. If no boundary is found, the BHC extends to the end of the note (flagged as "unbounded" in the output metadata). If the region between header and boundary is empty, extraction fails with reason `empty_section`.

This approach relies on the semi-structured format of MIMIC discharge summaries rather than attempting ML-based section classification. Notes with non-standard structure fail extraction with a labeled reason, which supports systematic analysis of failure modes.

## Header patterns

Four case-insensitive regex patterns match the BHC header, tried in order from most specific to least specific:

1. `brief hospital/hospitalization course` with optional prefixes ("summary of", "concise summary of", "brief summary of")
2. `hospital/hospitalization course` with the same optional prefixes plus "history of"
3. `nicu course` (neonatal ICU discharge summaries) with the optional summary prefix (but not "history of")
4. `hosp course` (with optional period)

The ordering matters. The extractor uses priority-based matching: it tries each pattern independently and uses the first match from the highest-priority pattern. This prevents less specific patterns from shadowing more specific headers that appear later in the text. For example, a note might contain "His hospital course has been complicated by..." as a sentence fragment early on, with the real section header "Brief Hospital Course:" further down. Priority matching tries the most specific pattern first, finds the real header, and never falls through to the bare "hospital course" pattern.

All patterns are anchored to the start of the line (`^` with `re.MULTILINE`) to prevent mid-sentence false positives, but not to the end of the line, so that headers with inline text ("HOSPITAL COURSE: The patient was admitted...") still match.

## End-boundary patterns

15 patterns identify top-level discharge summary sections that follow the BHC. These are organized into two confidence tiers:

**High confidence** (unambiguous as section headers): Medications on Admission, Medications on Discharge, Discharge Medications, Discharge Condition, Condition on Discharge, Discharge Diagnosis/Diagnoses, Discharge Instructions, Discharge Disposition, Discharge Status, Discharge Diet, Followup Instructions, Follow-up Instructions.

**Medium confidence** (legitimate sections, but words like "Facility" or "Attending" could appear within a BHC narrative): Facility, Completed by, Attending. The start-of-line anchor mitigates false positives.

The boundary list intentionally excludes BHC-internal sub-headers like "Cardiovascular:", "Renal:", or "Infectious Disease:". These appear inside domain-organized BHCs and must not terminate extraction. This is the key design tension: the boundary list must be broad enough to catch the next real section but narrow enough to avoid cutting into the BHC body.

## Addendum deduplication

MIMIC-III stores addendums (supplemental notes appended after the original discharge summary) as separate rows with the same hospital admission ID (`hadm_id`). Of the ~59,600 discharge summary rows, ~52,700 are unique by `hadm_id`; the rest are addendum duplicates.

The batch pipeline (`extract_from_file` / CLI) handles deduplication automatically: it extracts all notes for each admission, then keeps the one with the longest BHC text. The rationale, following Adams et al. (2021), is that the longest version is the most complete. MIMIC-IV-Note does not have this issue (one row per admission).

`extract_bhc()` operates on a single note and does not deduplicate. Deduplication is a property of the batch pipeline, not the per-note extraction.

## Quality flags

Each extraction result includes a list of quality flags:

- `below_min_length`: BHC text is shorter than 100 characters (threshold matching Aali et al., 2024).
- `unbounded`: No end boundary was found; BHC extends to the end of the note.
- `iserror`: The note was flagged as entered in error in MIMIC-III's `ISERROR` column.

These flags are informational. The extractor does not filter based on them; consumers decide what to keep.

## Structure classification

After extraction, the pipeline classifies each BHC's organizational structure into three categories:

- **Narrative**: Continuous prose following the patient's clinical trajectory, with no labeled sub-sections.
- **Domain-organized**: Divided into labeled sub-sections by clinical domain (organ system, disease, or management area). This is the majority format in both MIMIC-III (53%) and MIMIC-IV (64%).
- **Standardized template**: Fixed template with standardized section names (e.g., NICU assessment domains). Present only in MIMIC-III (4.5%).

The classifier also detects structural modifiers: acute/chronic partitions, transitional care sections, and numbering conventions.

Classification is implemented in `mimic_bhc/structure.py` and runs independently of the extraction step (it takes the extracted BHC text as input, not the original note).

## Format auto-detection

The batch pipeline auto-detects whether the input file is MIMIC-III or MIMIC-IV by checking the CSV header row for the presence of a `CATEGORY` column (present in MIMIC-III's NOTEEVENTS, absent in MIMIC-IV-Note's discharge table). Column names are normalized to lowercase internally. This can be overridden with the `--format` CLI flag.

## Pattern development process

The header and end-boundary pattern lists were not written in one pass. They evolved through a loop of literature review, code review, empirical discovery, and comparative validation.

**Step 1: Literature seeding.** The initial header and boundary lists came from published work on MIMIC section identification. Alsentzer & Kim (2018) established that "Brief Hospital Course" and "Hospital Course" are the dominant headers. Adams et al. (2021) documented the addendum deduplication strategy (keep longest per admission). Landes et al. (2022, MedSecId) and Aali et al. (2024, clin-bhc-summ) provided section header inventories used as starting points for the end-boundary list.

**Step 2: Code review of existing tools.** The Stanford clin-bhc-summ codebase (`utils/mimic_iv_bhc_preprocessing.py`) revealed 4 hardcoded header variants stripped via `.replace()` and a 19-header section list. Kruse et al.'s evaluation code (`evaluate_ds.py`) used 2 literal start patterns and 6 literal end patterns. Both tools informed pattern coverage but also revealed gaps: Stanford's patterns were case-sensitive, Kruse's were unanchored and missed common variants.

**Step 3: Empirical discovery.** The `discover` CLI command (`mimic-bhc-extract discover --input NOTEEVENTS.csv.gz`) samples discharge notes and scans for any line that looks like a section header (capitalized words on their own line). It returns each header with a frequency count and a role label ("BHC header", "End boundary", or "Other" based on the current pattern lists). The same logic is available programmatically via `discover_headers_from_file()` (takes a file path) or `discover_headers_in_texts()` (takes note strings directly) in `mimic_bhc/discovery.py`.

Running this on MIMIC-III samples surfaced header variants not in the literature:

- "Summary of Hospital Course", "Concise Summary of Hospital Course", "History of Hospital Course" (prefix variants)
- "NICU Course" (neonatal ICU discharge summaries)
- "Hosp Course" (abbreviation)

It also confirmed which discharge-summary sections reliably follow the BHC and which do not.

Each candidate was evaluated on the full corpus for extraction yield and false-positive risk. The start-of-line anchor (`^`) was the primary defense against false positives, making it safe to add patterns like "Hosp Course" that appear as standalone headers but never mid-paragraph. Candidates that matched common phrases within BHC narrative text were rejected.

**Step 4: Comparative validation.** Running the extractor on the full MIMIC-III corpus produced a higher extraction rate than published tools, confirming the broader pattern coverage. A head-to-head comparison with an alternative extractor (Kruse et al.) showed agreement on most cases, with our broader patterns capturing thousands of additional BHCs. The small number of cases where only the alternative succeeded were investigated to determine whether adding patterns was worth the false-positive risk. See [Empirical findings](empirical-findings.md) for the full comparison.

**Step 5: Targeted gap analysis.** The cases where only the alternative extractor succeeded traced to two root causes: PHI de-identification artifacts breaking the start-of-line anchor, and rare prefix variants not in our pattern list. The gap was too small to justify the added pattern complexity. Cases where both extractors failed were genuinely unstructured notes with no recognizable header in any form.

This loop (seed from literature, discover empirically, validate against published numbers, investigate gaps) is the process for extending or modifying the pattern lists. The `discover` CLI command is the primary tool for step 3. For extraction results on MIMIC-III and MIMIC-IV, header and boundary frequency tables, comparisons with published tools (Stanford's clin-bhc-summ, Kruse et al.), and known limitations, see [Empirical Findings](empirical-findings.md).
