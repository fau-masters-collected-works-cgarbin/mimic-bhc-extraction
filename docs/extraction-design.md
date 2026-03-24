# Extraction Design

How the Brief Hospital Course (BHC) extractor works: the algorithm, its assumptions, and the decisions behind them.

## What is the Brief Hospital Course

A discharge summary is a clinical document written when a patient leaves the hospital. It consolidates the entire admission into a single narrative that the next provider (primary care physician, rehabilitation facility, specialist) can use to continue care. Discharge summaries in MIMIC follow a semi-structured format with named sections, though the exact headers and ordering vary across institutions and individual clinicians.

The Brief Hospital Course (BHC) is typically the longest and most clinically dense section of the discharge summary. It narrates what happened during the hospitalization: why the patient was admitted, what diagnoses were made, what treatments were given, how the patient responded, and what the clinical trajectory looked like from admission to discharge. Clinicians write the BHC at the time of discharge, synthesizing information from daily progress notes, lab results, imaging reports, procedure notes, and medication records. The BHC is not a simple concatenation of these sources. It requires clinical judgment to select what matters, organize it coherently, and frame it for the receiving provider. In summarization research, the BHC is the target output, the section a model would generate given the rest of the clinical record as input (Adams et al., 2021; Aali et al., 2024).

## BHCResult as the single data structure

`BHCResult` is the canonical representation of a BHC extraction throughout the codebase. Every function that produces, transforms, or serializes extraction data works with `BHCResult` directly rather than building parallel dicts or DataFrames with ad-hoc field names.

`BHCResult` fields are populated in layers. Each layer fills in the fields it owns and passes the result forward using `dataclasses.replace()`:

1. **Extraction layer** (`extract_bhc()` in `mimic_bhc/extract.py`). Takes a note string, nothing else. Fills in all fields that can be derived directly from text extraction: the extracted text, matched headers, character offsets, and success/failure status. Patterns live in `mimic_bhc/patterns.py`.

2. **Enrichment layer** (callers like `_process_note()` and `_select_best_per_admission()` in `mimic_bhc/batch.py`). Adds context that the extraction layer does not have access to: record identifiers, note metadata, quality flags, and structure classification.

3. **Serialization layer** (`BHCResult.to_dict()` in `mimic_bhc/model.py`). Converts the dataclass to a dict for JSON/JSONL output. This is the only place where field names are mapped to their output representation. No other code constructs output dicts from `BHCResult` fields manually. `BHCResult.from_dict()` reverses the mapping for loading JSON back into objects.

Derived properties (character count, word count) are computed from the extracted text on access as `@property` methods, not stored as separate fields.

`compute_bhc_stats()` (in `mimic_bhc/stats.py`) takes a sequence of `BHCResult` objects directly, avoiding intermediate DataFrame conversions or field-name translation.

The design rule: if you need a new field on the extraction output, add it to `BHCResult` and populate it in the appropriate layer. Do not create parallel representations.

## Two-phase extraction

`extract_bhc()` operates on a single note string in two phases.

**Phase 1: Header search.** The extractor looks for a BHC section header using regex pattern matching. If no header is found, extraction fails with reason `no_header`.

**Phase 2: End-boundary search.** Starting from the text after the header, the extractor searches for the first line that matches a known end-boundary pattern (a top-level discharge summary section that comes after the BHC). If a boundary is found, the BHC is the text between the header and the boundary. If no boundary is found, the BHC extends to the end of the note (flagged as "unbounded" in the output metadata). If the region between header and boundary is empty, extraction fails with reason `empty_section`.

This approach relies on the semi-structured format of MIMIC discharge summaries rather than attempting ML-based section classification. Notes with non-standard structure fail extraction with a labeled reason, which lets consumers analyze failure modes systematically.

## Header patterns

Four case-insensitive regex patterns match the BHC header, tried in order from most specific to least specific:

1. `brief hospital/hospitalization course` with optional prefixes ("summary of", "concise summary of", "brief summary of")
2. `hospital/hospitalization course` with the same optional prefixes plus "history of"
3. `nicu course` (neonatal ICU discharge summaries) with the optional summary prefix (but not "history of")
4. `hosp course` (with optional period)

The ordering matters. The extractor tries each pattern independently and uses the first match from the highest-priority pattern. For example, a note might contain "His hospital course has been complicated by..." as a sentence fragment early on, with the real section header "Brief Hospital Course:" further down. Priority matching tries the most specific pattern first, finds the real header, and never falls through to the bare "hospital course" pattern.

All patterns are anchored to the start of the line (`^` with `re.MULTILINE`) to prevent mid-sentence false positives, but not to the end of the line, so that headers with inline text ("HOSPITAL COURSE: The patient was admitted...") still match.

## End-boundary patterns

15 patterns identify top-level discharge summary sections that follow the BHC. These are organized into two confidence tiers:

**High confidence** (unambiguous as section headers): Medications on Admission, Medications on Discharge, Discharge Medications, Discharge Condition, Condition on Discharge, Discharge Diagnosis/Diagnoses, Discharge Instructions, Discharge Disposition, Discharge Status, Discharge Diet, Followup Instructions, Follow-up Instructions.

**Medium confidence** (legitimate sections, but words like "Facility" or "Attending" could appear within a BHC narrative): Facility, Completed by, Attending. The start-of-line anchor mitigates false positives.

The boundary list intentionally excludes BHC-internal sub-headers like "Cardiovascular:", "Renal:", or "Infectious Disease:". These appear inside domain-organized BHCs and must not terminate extraction.

It also excludes handoff-oriented headers like "Transitional Issues" and "Pending Labs" that appear at the end of some BHCs (primarily in MIMIC-IV). Clinically, these are discharge planning rather than hospital course narrative, but clinicians type this content into the BHC text field in the EHR, so it falls between the BHC header and the next top-level boundary. The MIMIC-IV-Ext-BHC dataset (Aali et al., 2024) treats these the same way.

This is the key design tension: the boundary list must be broad enough to catch the next real section but narrow enough to avoid cutting into the BHC body.

## Addendum deduplication

MIMIC-III stores addendums (supplemental notes appended after the original discharge summary) as separate rows with the same hospital admission ID (`hadm_id`). Of the ~59,600 discharge summary rows, ~52,700 are unique by `hadm_id`. The rest are addendum duplicates.

The batch module (`extract_from_file` / CLI) handles deduplication automatically: it extracts all notes for each admission, then keeps the one with the longest BHC text. The rationale, following Adams et al. (2021), is that the longest version is the most complete. MIMIC-IV-Note does not have this issue (one row per admission).

`extract_bhc()` operates on a single note and does not deduplicate. Deduplication is a property of the batch module, not the per-note extraction.

## Quality flags

Each extraction result includes a list of quality flags:

- `below_min_length`: BHC text is shorter than 100 characters (threshold matching Aali et al., 2024).
- `unbounded`: No end boundary was found. BHC extends to the end of the note.
- `iserror`: The note was flagged as entered in error in MIMIC-III's `ISERROR` column.

These flags are informational. The extractor does not filter based on them. Consumers decide what to keep.

## Structure classification

After extraction, the batch module classifies each BHC's organizational structure into three categories. Classification is implemented in `mimic_bhc/structure.py` and runs independently of the extraction step (it takes the extracted BHC text as input, not the original note).

### Three-category taxonomy

Open-coding analysis of 50 BHC sections (30 MIMIC-III, 20 MIMIC-IV) produced a three-category taxonomy, validated through human review (42/50 cases, 100% agreement) and applied to both full datasets via the rule-based classifier. See [Empirical Findings](empirical-findings.md#structure-classification-distribution) for distribution rates.

**Narrative.** The BHC is written as continuous prose following the patient's clinical trajectory. Events are connected by temporal sequence rather than grouped by clinical domain. There are no labeled sections, numbered entries, or explicit structural divisions. Surgical notes that follow a procedure-recovery-discharge arc with POD numbering are typical examples.

**Domain-organized.** The BHC is divided into explicitly labeled sections, each covering one clinical domain (organ system, disease, or management area). Labels may appear as # prefixed headers, numbered entries, or standalone headers on their own lines. Within each section, clinicians write a narrative about that domain's course during the hospitalization. This is the majority format in both datasets.

**Standardized template.** The BHC follows a fixed template with standardized section names applied to every patient regardless of clinical presentation (e.g., NICU assessment domains: Respiratory, Cardiovascular, FEN, GI, Hematology, ID, Neurology, Sensory, Psychosocial). Present only in MIMIC-III (no neonatal admissions in MIMIC-IV).

The classifier detects all formats identically. Internal sub-headers (whether organ-system names, disease-specific labels, or template domains) sit inside the captured text and do not interfere with boundary detection.

### Structural modifiers

The classifier also detects four modifiers that capture within-category variation relevant to generation:

- **Mixed**: Combines elements of more than one format.
- **Numbering convention**: # vs 1. vs standalone.
- **Acute/chronic partition**: Explicit ACUTE ISSUES / CHRONIC ISSUES headers.
- **Transitional Issues section**: Forward-looking handoff content at the end of the BHC.

The Transitional Issues modifier tracks the handoff content that the end-boundary allowlist intentionally passes through (see [End-boundary patterns](#end-boundary-patterns)).

### Provenance of the format distinction

The domain-organized format traces its lineage to the problem-oriented medical record (POMR) introduced by Weed (1968), which organized clinical documentation around a numbered problem list with structured SOAP (Subjective, Objective, Assessment, Plan) entries for each problem. The POMR became one of the most influential ideas in clinical documentation and remains the basis for daily progress notes in most hospitals (Wright et al., 2014). When clinicians write a domain-organized BHC, they are applying the same organizational principle, grouping by clinical problem rather than following the temporal sequence of events.

Medical education literature recognizes "narrative" and "problem-based" approaches as distinct organizational strategies for the hospital course section. Dean et al. (2016) report an institutional template that formally adopted a "problem-based hospital course" format, integrating the diagnosis list into the course narrative rather than keeping it as a separate section.

Published work on BHC summarization (Adams et al., 2021; Xu et al., 2024) treats the BHC as an atomic text block without distinguishing its internal structure. Work on clinical note section detection (Denny et al., 2009; Pomares-Quimbaya et al., 2019) operates at the top-level section granularity (History of Present Illness, Hospital Course, Medications) and does not study sub-headers within the BHC itself.

### Why format matters for downstream tasks

The format distinction is significant for BHC generation, the downstream task this extraction supports. A model generating a narrative BHC needs to produce continuous prose. A model generating a domain-organized BHC needs to organize information by clinical problem, create appropriate sub-headers, and summarize each system separately. These are structurally different outputs from the same input data, with two practical consequences:

1. **Evaluation.** Metrics like ROUGE and BERTScore assume a single reference text. A narrative and a domain-organized BHC for the same admission could both be clinically correct yet score poorly against each other because of the structural difference. Domain-organized BHCs are roughly twice as long as narratives, creating additional length mismatch. The format choice is a legitimate stylistic variation, not an error, and evaluation should be stratified by format category.

2. **Training signal.** If training or fine-tuning on MIMIC BHCs, the model sees both formats mixed together with no label distinguishing them. This could lead to inconsistent output format, or the model might default to the majority format (domain-organized). Including the target format category in the generation prompt tells the model which organizational structure to follow.

## Format auto-detection

The batch module auto-detects whether the input file is MIMIC-III or MIMIC-IV by checking the CSV header row for the presence of a `CATEGORY` column (present in MIMIC-III's NOTEEVENTS, absent in MIMIC-IV-Note's discharge table). Column names are normalized to lowercase internally. This can be overridden with the `--format` CLI flag.

## Pattern development process

The header and end-boundary pattern lists were not written in one pass. They evolved through a loop of literature review, code review, empirical discovery, and comparative validation.

**Step 1: Literature seeding.** The initial header and boundary lists came from published work on identifying sections in MIMIC notes. Adams et al. (2021) documented the addendum deduplication strategy (keep longest per admission). Landes et al. (2022, MedSecId) and Aali et al. (2024, clin-bhc-summ) provided section header inventories used as starting points for the end-boundary list.

**Step 2: Code review of existing tools.** The Stanford clin-bhc-summ codebase (`utils/mimic_iv_bhc_preprocessing.py`) revealed 4 hardcoded header variants stripped via `.replace()` and a 19-header section list. Kruse et al.'s evaluation code (`evaluate_ds.py`) used 2 literal start patterns and 6 literal end patterns. Both tools informed pattern coverage but also revealed gaps: Stanford's patterns were case-sensitive, Kruse's were unanchored and missed common variants.

**Step 3: Empirical discovery.** The `discover` CLI command (`mimic-bhc-extract discover --input NOTEEVENTS.csv.gz`) samples discharge notes and scans for any line that looks like a section header (capitalized words on their own line). It returns each header with a frequency count and a role label ("BHC header", "End boundary", or "Other" based on the current pattern lists). The same logic is available programmatically via `discover_headers_from_file()` (takes a file path) or `discover_headers_in_texts()` (takes note strings directly) in `mimic_bhc/discovery.py`.

Running this on MIMIC-III samples surfaced header variants not in the literature:

- "Summary of Hospital Course", "Concise Summary of Hospital Course", "History of Hospital Course" (prefix variants)
- "NICU Course" (neonatal ICU discharge summaries)
- "Hosp Course" (abbreviation)

It also confirmed which discharge-summary sections reliably follow the BHC and which do not.

We evaluated each candidate on the full corpus for extraction yield and false-positive risk. The start-of-line anchor (`^`) was the primary defense against false positives, making it safe to add patterns like "Hosp Course" that appear as standalone headers but never mid-paragraph. Candidates that matched common phrases within BHC narrative text were rejected.

**Step 4: Comparative validation.** Running the extractor on the full MIMIC-III corpus produced a higher extraction rate than published tools, confirming the broader pattern coverage. A head-to-head comparison with an alternative extractor (Kruse et al.) showed agreement on most cases, with our broader patterns capturing thousands of additional BHCs. The small number of cases where only the alternative succeeded were investigated to determine whether adding patterns was worth the false-positive risk. See [Empirical Findings](empirical-findings.md) for the full comparison.

**Step 5: Targeted gap analysis.** The cases where only the alternative extractor succeeded traced to two root causes: PHI de-identification artifacts breaking the start-of-line anchor, and rare prefix variants not in our pattern list. The gap was too small to justify the added pattern complexity. Cases where both extractors failed were genuinely unstructured notes with no recognizable header in any form.

This loop (seed from literature, discover empirically, validate against published numbers, investigate gaps) is the process for extending or modifying the pattern lists. The `discover` CLI command is the primary tool for step 3. For extraction results on MIMIC-III and MIMIC-IV, header and boundary frequency tables, comparisons with published tools (Stanford's clin-bhc-summ, Kruse et al.), and known limitations, see [Empirical Findings](empirical-findings.md).

## Why regex, not existing tools

Two open-source tools extract sections from MIMIC clinical notes:

- **plandes/mimicsid** (Landes et al., COLING 2022): Full ML pipeline with trained section classifiers, SpaCy dependency, and model downloads. Designed for general section identification across all note types, not lightweight single-section extraction.
- **StanfordMIMI/clin-bhc-summ** (Aali et al., JAMIA 2024): Evaluation framework for clinical summarization. Extracts BHC as part of a larger pipeline but is not a reusable extraction library. See the [detailed comparison](empirical-findings.md#comparison-with-stanfordmimiclin-bhc-summ) in empirical findings.

We use a regex-based approach instead for two reasons. First, we need structured extraction metadata (success/failure reason, matched header variant, matched end boundary) to understand extraction reliability systematically, not just the extracted text. Neither tool provides this. Second, a simple regex extractor handles the task reliably (97.0% MIMIC-III, 88.4% MIMIC-IV), so pulling in an ML pipeline would add unnecessary complexity. We validate our extraction rates against published numbers from both tools as a sanity check.

## Updating documentation metrics

The documentation contains two kinds of numeric claims, each with a different update process.

### Code-derived metrics (automated)

Claims about pattern counts, thresholds, category names, and quality flag names are verified by `tests/test_doc_sync.py`. These tests parse the prose in this document and `README.md`, extract the claimed values, and compare them against the corresponding code constants. If you change a pattern list, threshold, or category set, the tests will fail until the documentation is updated to match (or vice versa).

Run `uv run pytest tests/test_doc_sync.py -v` to see which claims are covered. The test names and docstrings describe each verified claim.

### Data-derived metrics (manual)

Extraction success rates, header distributions, length statistics, structure classification percentages, and tool comparisons in [Empirical Findings](empirical-findings.md) come from running the pipeline on MIMIC-III and MIMIC-IV datasets. These datasets are not included in this repository (they require a PhysioNet credentialed access agreement).

To regenerate these metrics after code changes that could affect extraction results (new patterns, changed boundary logic, modified structure classification):

1. Run extraction on both datasets:

```bash
# Simplify the commands - replace with your paths
export MIMIC3_NOTES=../mimic-iii/physionet.org/files/mimiciii/1.4/
export MIMIC4_NOTES=../mimic-iv-note/physionet.org/files/mimic-iv-note/2.2/note/

uv run mimic-bhc-extract extract \
    --input $MIMIC3_NOTES/NOTEEVENTS.csv.gz --output results/bhc_mimic3.jsonl
uv run mimic-bhc-extract extract \
    --input $MIMIC4_NOTES/discharge.csv.gz --output results/bhc_mimic4.jsonl
```

2. Generate statistics:

```bash
uv run mimic-bhc-extract stats --input results/bhc_mimic3.jsonl
uv run mimic-bhc-extract stats --input results/bhc_mimic4.jsonl
```

3. Run the Kruse comparison (requires `--include-note-text` during extraction):

```bash
uv run mimic-bhc-extract extract \
    --input $MIMIC3_NOTES/NOTEEVENTS.csv.gz \
    --output results/bhc_mimic3_with_text.jsonl --include-note-text
uv run python scripts/compare_bhc_extraction_kruse.py \
    --input results/bhc_mimic3_with_text.jsonl
```

4. Update the tables in `docs/empirical-findings.md` with the new numbers. The header distribution tables list variants with count >= 100 individually and group the rest into "Other." Also update the structure classification percentages in this document (in the Structure classification section).

## References

- Dean, S. M., Gilmore-Bykovskyi, A., Buchanan, J., Ehlenfeldt, B., & Kind, A. J. H. (2016). Design and hospitalwide implementation of a standardized discharge summary in an electronic health record. The Joint Commission Journal on Quality and Patient Safety, 42(12), 555-AP11.
- Denny, J. C., Spickard, A., Johnson, K. B., Peterson, N. B., Peterson, J. F., & Miller, R. A. (2009). Evaluation of a method to identify and categorize section headers in clinical documents. JAMIA, 16(6), 806-815.
- Landes, P., et al. (2022). A New Public Corpus for Clinical Section Identification: MedSecId. COLING 2022.
- Pomares-Quimbaya, A., et al. (2019). Current approaches to identify sections within clinical narratives from electronic health records: a systematic review. BMC Medical Research Methodology, 19, 155.
- Weed, L. L. (1968). Medical records that guide and teach. New England Journal of Medicine, 278(11), 593-600.
- Wright, A., Sittig, D. F., McGowan, J., Ash, J. S., & Weed, L. L. (2014). Bringing science to medicine: an interview with Larry Weed, inventor of the problem-oriented medical record. JAMIA, 21(6), 964-968.
- Xu, S., et al. (2024). Overview of the First Shared Task on Clinical Text Generation: RRG24 and "Discharge Me!" BioNLP Workshop @ ACL 2024.
