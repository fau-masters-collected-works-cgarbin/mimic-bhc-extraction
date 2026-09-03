# Extraction Design

How the Brief Hospital Course (BHC) extractor works: the algorithm, its assumptions, and the decisions behind them.

## What is the Brief Hospital Course

A discharge summary is a clinical document written when a patient leaves the hospital. It consolidates the entire admission into a single narrative that the next provider (primary care physician, rehabilitation facility, specialist) can use to continue care. Discharge summaries in MIMIC follow a semi-structured format with named sections, though the exact headers and ordering vary across institutions and individual clinicians.

The Brief Hospital Course (BHC) is typically the longest and most clinically dense section of the discharge summary. It narrates what happened during the hospitalization: why the patient was admitted, what diagnoses were made, what treatments were given, how the patient responded, and what the clinical trajectory looked like from admission to discharge. Clinicians write the BHC at the time of discharge, synthesizing information from daily progress notes, lab results, imaging reports, procedure notes, and medication records. The BHC is not a simple concatenation of these sources. It requires clinical judgment to select what matters, organize it coherently, and frame it for the receiving provider. In summarization research, the BHC is the target output, the section a model would generate given the rest of the clinical record as input (Adams et al., 2021, and Aali et al., 2025).

## BHCResult as the single data structure

`BHCResult` is the canonical representation of a BHC extraction throughout the codebase. Every function that produces, transforms, or serializes extraction data works with `BHCResult` directly.

`BHCResult` fields are populated in layers. Each layer fills in the fields it owns and passes the result forward using `dataclasses.replace()`:

1. **Extraction layer** (`extract_bhc()` in `mimic_bhc/extract.py`). Takes a note string, nothing else. Fills in all fields that can be derived directly from text extraction: the extracted text, matched headers, character offsets, and success/failure status. Patterns live in `mimic_bhc/patterns.py`.

2. **Enrichment layer** (callers like `_process_note()` and `_select_best_per_admission()` in `mimic_bhc/batch.py`). Adds context that the extraction layer does not have access to: record identifiers, note metadata, quality flags, and structure classification.

3. **Serialization layer** (`BHCResult.to_dict()` in `mimic_bhc/model.py`). Converts the dataclass to a dict for JSON/JSONL output. This is the only place where field names are mapped to their output representation. No other code constructs output dicts from `BHCResult` fields manually. `BHCResult.from_dict()` reverses the mapping for loading JSON back into objects.

Derived properties (character count, word count) are `@property` methods, computed from the extracted text on access.

The design rule: if you need a new field on the extraction output, add it to `BHCResult` and populate it in the appropriate layer. Do not create parallel representations.

## Two-phase extraction

`extract_bhc()` operates on a single note string in two phases.

**Phase 1: Header search.** The extractor looks for a BHC section header using regex pattern matching. If no header is found, extraction fails with reason `no_header`.

**Phase 2: End-boundary search.** Starting from the text after the header, the extractor searches for the first line that matches a known end-boundary pattern (a top-level discharge summary section that comes after the BHC). If a boundary is found, the BHC is the text between the header and the boundary. If no boundary is found, the BHC extends to the end of the note (flagged as "unbounded" in the output metadata). If the region between header and boundary is empty, extraction fails with reason `empty_section`.

This approach relies on the semi-structured format of MIMIC discharge summaries. Notes with non-standard structure fail extraction with a labeled reason, which lets consumers analyze failure modes systematically.

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

| Confidence | Boundary |
|------------|----------|
| High | Medications on Admission |
| High | Medications on Discharge |
| High | Discharge Medications |
| High | Discharge Condition |
| High | Condition on Discharge |
| High | Discharge Diagnosis/Diagnoses |
| High | Discharge Instructions |
| High | Discharge Disposition |
| High | Discharge Status |
| High | Discharge Diet |
| High | Followup Instructions |
| High | Follow-up Instructions |
| Medium | Facility |
| Medium | Completed by |
| Medium | Attending |

High-confidence boundaries are unambiguous as section headers. The medium-confidence three are legitimate sections whose words ("Facility", "Attending") could also appear within a BHC narrative. The start-of-line anchor mitigates that. `tests/test_doc_sync.py` checks this table against `_END_BOUNDARIES`.

The boundary list excludes BHC-internal sub-headers like "Cardiovascular:", "Renal:", or "Infectious Disease:". These appear inside domain-organized BHCs and must not terminate extraction.

It also excludes handoff-oriented headers like "Transitional Issues" and "Pending Labs" that appear at the end of some BHCs (primarily in MIMIC-IV). Clinically, this content is discharge planning, but clinicians type it into the BHC text field in the EHR, so it falls between the BHC header and the next top-level boundary. The released MIMIC-IV-Ext-BHC data retains this content in its BHC field, though Aali et al. (2025) do not document it as a design choice.

The boundary list must be broad enough to catch the next real section but narrow enough to avoid cutting into the BHC body.

## Addendum deduplication

MIMIC-III stores addendums (supplemental notes appended after the original discharge summary) as separate rows with the same hospital admission ID (`HADM_ID`). Of the ~59,600 discharge summary rows, ~52,700 are unique by `hadm_id`. The rest are addendum duplicates.

The batch module (`extract_from_file` / CLI) handles deduplication automatically: it extracts all notes for each admission, then keeps the one with the longest BHC text, on the rationale that the longest version is the most complete. MIMIC-IV-Note does not have this issue (one row per admission).

## Quality flags

Each extraction result includes a list of quality flags:

- `below_min_length`: BHC text is shorter than 100 characters, matching the MIMIC-IV-Ext-BHC PhysioNet documentation. The threshold is not in Aali et al.'s paper or code.
- `unbounded`: No end boundary was found. BHC extends to the end of the note.
- `iserror`: The note was flagged as entered in error in MIMIC-III's `ISERROR` column.

These flags are informational. The extractor does not filter based on them. Consumers decide what to keep.

## Structure classification

After extraction, the batch module classifies each BHC's organizational structure into three categories. Classification is implemented in `mimic_bhc/structure.py` and runs independently of the extraction step (it takes the extracted BHC text as input).

### Three-category taxonomy

**Narrative.** The BHC is written as continuous prose following the patient's clinical trajectory. Events follow the temporal sequence in which they happened. There are no labeled sections, numbered entries, or explicit structural divisions. Surgical notes that follow a procedure-recovery-discharge arc with POD (post-operative day) numbering are typical examples.

**Domain-organized.** The BHC is divided into explicitly labeled sections, each covering one clinical domain (organ system, disease, or management area). Labels may appear as # prefixed headers, numbered entries, or unprefixed headers that open a line and are followed by the domain's text. Within each section, clinicians write a narrative about that domain's course during the hospitalization. This is the majority format in both datasets.

**Standardized template.** The BHC follows a fixed template with standardized section names applied to every patient regardless of clinical presentation (e.g., NICU assessment domains: Respiratory, Cardiovascular, FEN (fluids, electrolytes, nutrition), GI (gastrointestinal), Hematology, ID (infectious disease), Neurology, Sensory, Psychosocial). Present only in MIMIC-III (no neonatal admissions in MIMIC-IV).

See [Empirical Findings](empirical-findings.md#structure-classification-distribution) for how the three categories are distributed across the two datasets.

### Validating the classifier against hand-coded cases

`docs/bhc-format-coding-rules.md` states the rules the classifier implements. One author hand-coded 40 blinded cases against those rules and disagreed with the classifier on one of them.

The 40 cases cover two rules and are scored separately: 30 cases for the category rule, which accepts up to 3 disagreements, and 10 for the acute/chronic rule, which accepts 1.

The coding rules give the coder two labels, narrative and domain-organized, and tell them to code a neonatal template as domain-organized. The hand-coding therefore validates only the boundary between narrative and domain-organized.

`scripts/sample_structure_validation.py` draws the sample and writes the coding sheet and the answer key.

### Structural modifiers

The classifier emits two modifiers alongside the category:

- **Acute/chronic partition**: a header on its own line labeling a group of problems acute, active, chronic, stable, or inactive.
- **Transitional Issues section**: Forward-looking handoff content at the end of the BHC.

It also records a numbering convention for domain-organized BHCs: `#`, `##`, `N.`, `N)`, `(N)`, `N/`, `*`, `standalone`, or `mixed`. `standalone` means the header has no prefix marker. That is a field on the result, and nothing reads it.

The Transitional Issues modifier tracks the handoff content that the end-boundary allowlist passes through (see [End-boundary patterns](#end-boundary-patterns)).

### Provenance of the format distinction

The domain-organized format traces its lineage to the problem-oriented medical record (POMR). Weed introduced it in 1964 and popularized it in his 1968 NEJM article. The POMR organizes clinical documentation around a numbered problem list. Each progress note is filed under a numbered problem, recording the patient's subjective account, the objective data, the current treatment, and the plan for the next interval (Weed, 1968). Later work replaced the treatment element with an assessment and codified the result as SOAP. Weed's paper is among the most cited in clinical informatics, and the problem list and the SOAP-format progress note are now described as ubiquitous (Wright et al., 2014). When clinicians write a domain-organized BHC, they group information by clinical problem, the same principle the POMR applies to progress notes.

Hospital medicine literature treats problem-based organization of the hospital course as a template choice. Smith et al. (2017) report a discharge summary redesign that eliminated the stand-alone diagnosis list and incorporated it "as part of the problem-based hospital course," and they score "problem-based hospital course" as one of 24 discharge summary quality elements. Dean et al. (2016), standardizing discharge summaries hospitalwide at a different institution, keep diagnoses and hospital course as separate template elements.

We found no published work that names the narrative format as a category or contrasts it with the problem-based one. The narrative category here comes from the corpus.

Adams et al. (2021) observe that most BHCs are "written in a problem-oriented fashion (Weed, 1968)," but treat the observation as a corpus characteristic: they do not categorize the variation, label it, or condition on it. Other BHC summarization work (Xu et al., 2024) treats the BHC as an atomic text block.

### Why format matters for BHC generation

The format distinction is significant for BHC generation, the task this extraction supports. A model generating a narrative BHC needs to produce continuous prose. A model generating a domain-organized BHC needs to organize information by clinical problem, create appropriate sub-headers, and summarize each system separately. These are structurally different outputs from the same input data, with two practical consequences:

1. **Evaluation.** Metrics like ROUGE and BERTScore assume a single reference text. A narrative and a domain-organized BHC for the same admission could both be clinically correct yet score poorly against each other because of the structural difference. Domain-organized BHCs are roughly twice as long as narratives, creating additional length mismatch. The format choice is a legitimate stylistic variation. Evaluation should be stratified by format category.

2. **Training signal.** If training or fine-tuning on MIMIC BHCs, the model sees both formats mixed together with no label distinguishing them. This could lead to inconsistent output format, or the model might default to the majority format (domain-organized). Including the target format category in the generation prompt tells the model which organizational structure to follow.

## Dataset format auto-detection

The batch module auto-detects whether the input file is MIMIC-III or MIMIC-IV by checking the CSV header row for the presence of a `CATEGORY` column (present in MIMIC-III's NOTEEVENTS, absent in MIMIC-IV-Note's discharge table). Column names are normalized to lowercase internally. This can be overridden with the `--format` CLI flag.

## Pattern development process

The header and end-boundary pattern lists were not written in one pass. They evolved through a loop of literature review, code review, empirical discovery, and comparative validation.

**Step 1: Literature seeding.** The initial end-boundary list came from published work on identifying sections in MIMIC notes. Landes et al. (2022, MedSecId) and Aali et al. (2025, clin-bhc-summ) provided section header inventories used as starting points. The header patterns came later, from step 3, because no published inventory covers the BHC header variants MIMIC-III actually uses.

**Step 2: Code review of existing tools.** The Stanford clin-bhc-summ codebase (`utils/mimic_iv_bhc_preprocessing.py`) revealed 4 hardcoded header variants stripped via `.replace()` and a 19-header section list. Kruse et al.'s evaluation code (`evaluate_ds.py`) used 2 literal start patterns and 6 literal end patterns. Both tools informed pattern coverage but also revealed gaps: Stanford's patterns were case-sensitive, Kruse's were unanchored and missed common variants.

The clin-sum codebase behind Adams et al. (2021) publishes the extraction used to build their dataset (`preprocess/section_utils.py`, `extract_hospital_course()`). It inverts our approach: it detects headers generically and selects them by substring. See the [detailed comparison](empirical-findings.md#comparison-with-adams-et-al-clin-sum) in empirical findings.

**Step 3: Empirical discovery.** The `discover` CLI command (`mimic-bhc-extract discover --input NOTEEVENTS.csv.gz`) samples discharge notes and scans for any line that looks like a section header (capitalized words on their own line). It returns each header with a frequency count and a role label ("BHC header", "End boundary", or "Other" based on the current pattern lists). The same logic is available programmatically via `discover_headers_from_file()` (takes a file path) or `discover_headers_in_texts()` (takes note strings directly) in `mimic_bhc/discovery.py`.

Running this on MIMIC-III samples found header variants not in the literature:

- "Summary of Hospital Course", "Concise Summary of Hospital Course", "History of Hospital Course" (prefix variants)
- "NICU Course" (neonatal ICU discharge summaries)
- "Hosp Course" (abbreviation)

It also confirmed which discharge-summary sections reliably follow the BHC and which do not.

We evaluated each candidate on the full corpus for extraction yield and false-positive risk. The start-of-line anchor (`^`) was the primary defense against false positives, making it safe to add patterns like "Hosp Course" that appear as headers on their own line but never mid-paragraph. Candidates that matched common phrases within BHC narrative text were rejected.

**Step 4: Comparative validation.** We ran our extractor and Kruse et al.'s over the same MIMIC-III notes. The two agree on most cases. Our broader patterns capture thousands of additional BHCs. The small number of cases where only the alternative succeeded were investigated to determine whether adding patterns was worth the false-positive risk. See [Empirical Findings](empirical-findings.md) for the full comparison.

**Step 5: Targeted gap analysis.** The cases where only the alternative extractor succeeded traced to two root causes: PHI de-identification artifacts breaking the start-of-line anchor, and rare prefix variants not in our pattern list. The gap was too small to justify the added pattern complexity. Cases where both extractors failed were genuinely unstructured notes with no recognizable header in any form.

This loop (seed from literature, discover empirically, validate against published numbers, investigate gaps) is the process for extending or modifying the pattern lists. For extraction results on MIMIC-III and MIMIC-IV, header and boundary frequency tables, comparisons with published tools (Stanford's clin-bhc-summ, Kruse et al.), and known limitations, see [Empirical Findings](empirical-findings.md).

## Choosing regex over existing tools

Two open-source tools are packaged for reuse on MIMIC clinical notes:

- **plandes/mimicsid** (Landes et al., COLING 2022): Full ML pipeline with trained section classifiers, SpaCy dependency, and model downloads. Designed for general section identification across all note types.
- **StanfordMIMI/clin-bhc-summ** (Aali et al., JAMIA 2025): Evaluation framework for clinical summarization. Extracts BHC as part of a larger pipeline but is not a reusable extraction library. See the [detailed comparison](empirical-findings.md#comparison-with-aali-et-al-clin-bhc-summ) in empirical findings.

We use a regex-based approach instead for two reasons. First, understanding extraction reliability systematically requires structured extraction metadata (success/failure reason, matched header variant, matched end boundary) alongside the extracted text. Neither tool provides this. Second, a simple regex extractor handles the task reliably (97.0% MIMIC-III, 88.4% MIMIC-IV), so pulling in an ML pipeline would add unnecessary complexity.

The one direct check is against Kruse et al.: run over the same MIMIC-III notes, our extractor succeeds on 97.0% and theirs on 85.1%. Aali et al.'s 81.4% counts what survives their full pipeline. These rates are checked against [Empirical Findings](empirical-findings.md) by `tests/test_doc_sync.py`.

## Updating documentation metrics

The documentation contains two kinds of numeric claims, each with a different update process.

### Code-derived metrics (automated)

Claims about pattern counts, thresholds, and end-boundary names are verified by `tests/test_doc_sync.py`. These tests parse the prose and tables in this document and `docs/bhc-format-coding-rules.md`, extract the claimed values, and compare them against the corresponding code constants. One test also checks that every success rate quoted in this document appears in `docs/empirical-findings.md`. The coding-rules tests go further and run the classifier on every worked example in that document, so an example the classifier reads differently fails the build. If you change a pattern list or a threshold, the tests will fail until the documentation is updated to match (or vice versa).

Run `uv run pytest tests/test_doc_sync.py -v` to see which claims are covered. The test names and docstrings describe each verified claim.

### Data-derived metrics (manual)

Extraction success rates, header distributions, length statistics, structure classification percentages, and tool comparisons in [Empirical Findings](empirical-findings.md) come from running the pipeline on MIMIC-III and MIMIC-IV datasets. These datasets are not included in this repository (they require a PhysioNet credentialed access agreement).

To regenerate these metrics after code changes that could affect extraction results (new patterns, changed boundary logic, modified structure classification):

1. Run extraction on both datasets:

```bash
# Set these to your paths to shorten the commands below
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

4. Update the tables in `docs/empirical-findings.md` with the new numbers. The header distribution tables list variants with count >= 100 individually and group the rest into "Other." The `stats` command's structure distribution supplies both the category prevalence table and the median length per format, so take both from the same run. Do not mix in figures from `scripts/bhc_format_stats.py`: it reports over the structure-eligible pool (extractions longer than 500 characters) and folds standardized templates into domain-organized, so its shares and medians will not match the `stats` output.

5. Check three claims in [Structure classification](#structure-classification) against the new distribution: that domain-organized is the majority format in both datasets, that standardized templates appear only in MIMIC-III, and that domain-organized BHCs run roughly twice as long as narrative ones. No test covers them.

## References

- Aali, A., Van Veen, D., Arefeen, Y. I., et al. (2025). A dataset and benchmark for hospital course summarization with adapted large language models. JAMIA, 32(3), 470-479. doi:10.1093/jamia/ocae312.
- Adams, G., Alsentzer, E., Ketenci, M., Zucker, J., & Elhadad, N. (2021). What's in a Summary? Laying the Groundwork for Advances in Hospital-Course Summarization. NAACL 2021, 4794-4811. doi:10.18653/v1/2021.naacl-main.382.
- Dean, S. M., Gilmore-Bykovskyi, A., Buchanan, J., Ehlenfeldt, B., & Kind, A. J. H. (2016). Design and hospitalwide implementation of a standardized discharge summary in an electronic health record. The Joint Commission Journal on Quality and Patient Safety, 42(12), 555-AP11. doi:10.1016/S1553-7250(16)30107-6.
- Kruse, M., Hu, S., Derby, N., et al. (2025). Large Language Models with Temporal Reasoning for Longitudinal Clinical Summarization and Prediction. Findings of EMNLP 2025, 20715-20735. doi:10.18653/v1/2025.findings-emnlp.1128. Preprint: arXiv:2501.18724.
- Landes, P., Patel, K., Huang, S. S., Webb, A., Di Eugenio, B., & Caragea, C. (2022). A New Public Corpus for Clinical Section Identification: MedSecId. COLING 2022, 3709-3721.
- Smith, C. J., Beachy, M., Vasey, A., Langenhan, T., & Shiffermiller, J. (2017). Evaluation of an Enhanced Discharge Summary Template: Building a Better Handoff Document. Journal of Clinical Outcomes Management, 24(12), 542-550.
- Weed, L. L. (1964). Medical records, patient care, and medical education. Irish Journal of Medical Science, 39(6), 271-282. doi:10.1007/BF02945791.
- Weed, L. L. (1968). Medical records that guide and teach. New England Journal of Medicine, 278(11), 593-600 (part I), and 278(12), 652-657 (part II). doi:10.1056/NEJM196803142781105, doi:10.1056/NEJM196803212781204.
- Wright, A., Sittig, D. F., McGowan, J., Ash, J. S., & Weed, L. L. (2014). Bringing science to medicine: an interview with Larry Weed, inventor of the problem-oriented medical record. JAMIA, 21(6), 964-968. doi:10.1136/amiajnl-2014-002776.
- Xu, J., et al. (2024). Overview of the First Shared Task on Clinical Text Generation: RRG24 and "Discharge Me!" Proceedings of the 23rd Workshop on Biomedical Natural Language Processing (BioNLP 2024), 85-98. doi:10.18653/v1/2024.bionlp-1.7.
