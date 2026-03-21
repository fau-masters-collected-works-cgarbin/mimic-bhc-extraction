# Empirical Findings

Extraction results, tool comparisons, and known limitations from running the extractor on MIMIC-III v1.4 and MIMIC-IV-Note v2.2.

## MIMIC-III Results

### Dataset summary

MIMIC-III NOTEEVENTS includes an ISERROR column that flags notes entered in error (filed to the wrong patient, retracted, etc.). The extraction tool does not exclude these rows; it flags them with an `iserror` quality flag so consumers can decide whether to keep them. For the analysis below, ISERROR rows were excluded to produce clean statistics. Prior work on MIMIC-III does not consistently document whether ISERROR rows were excluded, which introduces a small source of incomparability in published row counts.

MIMIC-III also stores addendums as separate rows with the same hospital admission ID (HADM_ID) as the original discharge summary. An addendum is a supplemental note appended after the original, typically containing corrections or late results. Of the 59,652 discharge summary rows, 6,926 are duplicates by HADM_ID. We deduplicate by keeping the longest note per admission, following Adams et al. (2021), on the rationale that the longest version is the most complete.

| Metric | Count |
|--------|------:|
| Total discharge summary rows (after ISERROR filter) | 59,652 |
| Unique hospital admissions (after addendum dedup) | 52,726 |
| Addendum rows removed (extra rows per admission) | 6,926 |

### Extraction results

| Metric | Value |
|--------|------:|
| Successful extractions | 51,131 |
| Failed extractions | 1,595 |
| Success rate | 97.0% |
| Failure reason (all failures) | `no_header` |

All failures are `no_header`. No `empty_section` failures were observed. The 1,595 failed notes are genuinely unstructured: they lack any recognizable BHC header variant and use a narrative format without section delimiters.

### Comparison with published numbers

Our 97.0% extraction rate exceeds the ~87% reported by Alsentzer & Kim (2018) for MIMIC-III. The improvement comes from two design choices: (1) allowing inline text after the header colon (many notes write "HOSPITAL COURSE: The patient was admitted..." on one line rather than separating the header), and (2) matching a broader set of header variants including "Summary of Hospital Course", "Concise Summary of Hospital Course", "NICU Course", "History of Hospital Course", and "Hospitalization Course".

Adams et al. (2021) report ~109K source-target pairs from MIMIC-III. This number is larger than the ~59K total discharge summary rows in NOTEEVENTS, so it likely refers to source-target pairs in their summarization dataset (where the "source" is the collection of clinical notes for an admission and the "target" is the BHC), not to extracted BHC sections. We cannot directly compare our extraction count to their 109K.

### Header distribution

The top header variants, from 51,131 successful extractions:

| Header variant | Count | % |
|----------------|------:|--:|
| Brief Hospital Course: | 37,980 | 74.3% |
| HOSPITAL COURSE: | 9,435 | 18.5% |
| HOSPITAL COURSE (no colon) | 1,420 | 2.8% |
| SUMMARY OF HOSPITAL COURSE | 1,264 | 2.5% |
| CONCISE SUMMARY OF HOSPITAL COURSE | 291 | 0.6% |
| BRIEF SUMMARY OF HOSPITAL COURSE | 213 | 0.4% |
| HISTORY OF HOSPITAL COURSE | 176 | 0.3% |
| BRIEF HOSPITAL COURSE: (all-caps) | 113 | 0.2% |
| NICU COURSE | 71 | 0.1% |
| Other variants | 168 | 0.3% |

"Brief Hospital Course:" (74.3%) is the standard header. "HOSPITAL COURSE:" (18.5%) is the main alternative.

### End-boundary distribution

| Boundary | Count | % |
|----------|------:|--:|
| Medications on Admission | 37,094 | 72.6% |
| Discharge Diagnosis/Diagnoses | 6,103 | 11.9% |
| (no boundary, extracted to end of note) | 3,772 | 7.4% |
| Discharge Medications | 2,799 | 5.5% |
| Medications on Discharge | 915 | 1.8% |
| Discharge Instructions | 203 | 0.4% |
| Discharge Disposition | 199 | 0.4% |
| Other (Condition on Discharge, Facility, Discharge Status, Follow-up, Attending, Diet) | 46 | 0.1% |

"Medications on Admission" is the dominant end boundary (72.6%), listing the patient's pre-admission medications between the BHC narrative and the discharge medications section.

3,772 extractions (7.4%) had no end boundary and were extracted to the end of the note. Manual inspection of a sample showed they consistently end with dictation metadata (e.g., "Dictated By:", "MEDQUIST36", "Job#") rather than clinical content from a subsequent section. These appear to be notes where the BHC is the last clinical section, followed only by dictation footers not in the end-boundary allowlist. The dictation metadata is included in the extracted text; downstream consumers may want to strip it.

### BHC length distribution

| Statistic | Characters |
|-----------|----------:|
| Mean | 3,015 |
| Median | 2,521 |
| Q25 | 1,544 |
| Q75 | 3,940 |
| Min | 7 |
| Max | 34,170 |
| Short (<100 chars) | 18 |

18 extractions are shorter than the 100-character threshold. Lengths are reported in characters, not tokens, to avoid baking in a specific tokenizer choice at the extraction stage.

These aggregate statistics mix narrative and domain-organized BHCs, which differ in length by roughly 2x. A separate structural analysis found median lengths of 1,669 chars (narrative) vs 3,241 chars (domain-organized) in MIMIC-III.

### Verification

Manual inspection of 10 randomly selected successful extractions confirmed correct boundary detection in all cases: BHC text started immediately after the header and ended at the expected section boundary. No truncation (boundary firing too early) or over-extraction (capturing the next section) was observed. The "Facility" and "Attending" medium-confidence end boundaries did not cause premature truncation in any inspected case.

## MIMIC-IV-Note Results

Results from running the same extractor on MIMIC-IV-Note v2.2 discharge summaries, for comparison with MIMIC-III results and with Aali et al. (2024).

### Dataset summary

MIMIC-IV-Note does not require ISERROR filtering (erroneous notes were removed from the dataset) or addendum deduplication (each row is a unique discharge summary).

| Metric | Count |
|--------|------:|
| Total discharge summaries | 331,793 |

### Extraction results

| Metric | Value |
|--------|------:|
| Successful extractions | 293,329 |
| Failed extractions | 38,464 |
| Success rate | 88.4% |
| Failure reason: `no_header` | 38,459 |
| Failure reason: `empty_section` | 5 |

Our 88.4% is a pure extraction success rate: we attempt extraction on all 331,793 notes and report how many succeed. Aali et al.'s 81.4% (270,033/331,794) is the output of their full pipeline, which includes a structural pre-filter, extraction, and a minimum length threshold. We cannot decompose their lower rate into per-stage losses because the intermediate counts are not published.

The higher failure rate on MIMIC-IV (11.6%) compared to MIMIC-III (3.0%) reflects a larger proportion of notes that lack any recognizable BHC header. Inspection of 20 failed notes showed 13 with standard section headers (Chief Complaint, History of Present Illness, etc.) but no BHC section, suggesting these notes use a template that omits the Brief Hospital Course as a named section. Aali et al.'s structural pre-filter (`text[88:91] == 'Sex'`) would exclude many of these non-standard notes before extraction, which likely explains part of the rate difference.

Five `empty_section` failures were observed, where a BHC header was found but contained no text before the next section boundary.

### Header distribution

| Header variant | Count | % |
|----------------|------:|--:|
| Brief Hospital Course: | 291,137 | 99.3% |
| All other variants | 2,195 | 0.7% |

"Brief Hospital Course:" is even more dominant in MIMIC-IV (99.3%) than in MIMIC-III (74.2%). Broader header matching is not a significant factor for MIMIC-IV: the additional variants contribute only ~2,200 extractions.

### End-boundary distribution

| Boundary | Count | % |
|----------|------:|--:|
| Medications on Admission | 276,538 | 94.3% |
| Discharge Medications | 15,343 | 5.2% |
| Discharge Disposition | 799 | 0.3% |
| (no boundary) | 437 | 0.1% |
| Other | 212 | 0.1% |

"Medications on Admission" is the dominant boundary in both datasets. Only 437 unbounded extractions (0.1%), compared to 3,772 (7.4%) in MIMIC-III.

### BHC length distribution

| Statistic | Characters |
|-----------|----------:|
| Mean | 2,427 |
| Median | 1,978 |
| Q25 | 1,267 |
| Q75 | 3,121 |
| Min | 1 |
| Max | 37,171 |
| Short (<100 chars) | 220 |

Mean BHC length is 2,427 characters, compared to 3,015 in MIMIC-III. The different extraction success rates (88.4% vs. 97.0%) mean the two populations are not identical, so the length difference may partly reflect which notes were successfully extracted rather than a true difference in BHC length across datasets. For rough comparability with Aali et al.'s 564 ± 410 tokens, assuming ~4 characters per token, our mean would be ~607 tokens, in the same range but not directly comparable without using the same tokenizer.

### Verification

Manual inspection of 10 randomly selected successful extractions confirmed correct boundary detection. No truncation or over-extraction was observed.

## Comparison with StanfordMIMI/clin-bhc-summ

A detailed review of the Stanford code and the associated paper (Aali et al., JAMIA 2024).

**Extraction methodology is underspecified.** Neither the paper nor the repo publish the actual extraction regex. The paper describes preprocessing as "whitespace removal, uniform spacing, section identification, header standardization, extraneous symbol removal, and tokenization" using regex and NLTK, but the specific patterns are not disclosed. The repo (`utils/mimic_iv_bhc_preprocessing.py`) contains only post-extraction cleanup of an already-extracted BHC column. We cannot directly compare extraction strategies, only validate against their published dataset statistics.

**Drop rate is significant but unexplained.** They started with 331,794 MIMIC-IV-Note discharge summaries and produced 270,033 note-BHC pairs, dropping ~19% of notes. The paper does not break down why notes were dropped (missing BHC header? malformed structure? minimum length filter?). Our structured extraction metadata can address this gap.

**Header handling.** Their post-extraction cleanup strips four hardcoded header variants via `.replace()` calls: "Brief Hospital Course:\n", "BRIEF HOSPITAL COURSE:\n", "BRIEF HOSPITAL COURSE", "Brief Hospital Course". Our extractor uses four case-insensitive regex patterns that match these plus additional variants: "hospital course", "hospitalization course", "nicu course", "hosp course", each with optional prefixes like "summary of", "concise summary of", "history of". We preserve the matched header as metadata rather than stripping it.

**End boundaries.** Their code lists 19 section headers as categories, which would serve as end boundaries during extraction. This list includes headers for sections that typically appear before the BHC (Chief Complaint, History of Present Illness, Physical Exam, etc.), useful for a general section parser but unnecessary for BHC-specific extraction. Our allowlist has 15 patterns focused specifically on sections that follow the BHC.

**Note filtering.** They apply a structural format check (`text[88:91] == 'Sex'`) to keep only notes following the standard EHR template. MIMIC-IV-Note discharge summaries with the standard template start with a fixed-format preamble (~88 characters), followed by "Sex:" as the first structured field. Notes passing this check are likely to have all subsequent section headers in the standard format. This is a brittle heuristic (it assumes a fixed preamble length) but would pre-filter to notes following the standard template. We do not apply this filter, which means we attempt extraction on all discharge summaries including poorly structured ones. This may explain differences in extraction rates.

**Minimum length threshold.** Their PhysioNet documentation mentions excluding BHC pairs with fewer than 100 characters, though this threshold does not appear in the published code. We flag extractions shorter than 100 characters but do not exclude them.

**Extraction metadata.** Their pipeline produces only the extracted text. Ours returns structured results (success/failure reason, matched header variant, matched end boundary, note_row_id) that support systematic quality analysis.

### Summary

The comparison confirms that our regex-based approach is reasonable and in some ways more thorough than published work: broader header coverage (case-insensitive matching with abbreviated variants), addendum deduplication that the Stanford code lacks, and structured extraction metadata for quality analysis. Specifically, our failure-reason breakdown can explain the ~19% drop rate from the Stanford paper.

## Comparison with Kruse et al.

Kruse et al. ("Large Language Models with Temporal Reasoning for Longitudinal Clinical Summarization and Prediction," arXiv:2501.18724) use a minimal regex to extract the BHC from MIMIC-III discharge summaries at evaluation time. Their extraction is not a data preparation step: the full discharge summary is saved verbatim as the gold standard, and section slicing happens only when computing metrics. The extraction logic is in `evaluation/evaluate_ds.py::extract_sections()` ([source](https://github.com/LARK-NLP-Lab/longitudinal_clinical_summarization/blob/main/evaluation/evaluate_ds.py)).

**Their approach.** Two start patterns (`Brief Hospital Course:` and `HOSPITAL COURSE:`), six end patterns (`Discharge Medications:`, `DISCHARGE STATUS:`, `Medications on Admission:`, `CONDITION ON DISCHARGE:`, `DISCHARGE DIAGNOSES:`, `DISCHARGE CONDITION:`). BHC is the text between the end of the start marker and the beginning of the first end marker. No fallback if either is missing (returns empty string). No start-of-line anchor on the start pattern. No case-insensitive matching (the two patterns are literal case variants).

### Design differences

| Dimension | Ours | Kruse |
|-----------|------|-------|
| Start patterns | 4 case-insensitive regexes with priority ordering | 2 literal strings |
| Start anchoring | `^` (start of line, `re.MULTILINE`) | None (matches anywhere in text) |
| End patterns | 15 patterns, start-of-line anchored | 6 literal strings, unanchored |
| Missing boundary | Extracts to end of note | Returns empty string |
| Case handling | Case-insensitive (`re.IGNORECASE`) | Case-sensitive (two literal variants) |
| Header variants | "summary of", "concise summary of", "history of", "nicu course", "hosp. course" | None |
| Addendum handling | Extracts from all rows, keeps longest BHC per hadm_id | Not applicable (full note saved verbatim) |

### Empirical comparison

Both extractors run on the same text for each of the 52,726 MIMIC-III admissions, after deduplicating to the longest note per hadm_id:

| Metric | Ours | Kruse |
|--------|------|-------|
| Success rate | 97.0% (51,131) | 85.0% (44,839) |

Agreement breakdown (from a prior run at 96.7% success rate; the agreement table has not been re-run after the "Condition on Discharge" end-boundary addition):

| Category | Count | % |
|----------|------:|--:|
| Both succeed | 44,797 | 85.0% |
| Only ours succeeds | 6,191 | 11.7% |
| Only Kruse succeeds | 42 | 0.1% |
| Both fail | 1,696 | 3.2% |

When both succeed (44,797 cases): 88.8% have near-exact text overlap (SequenceMatcher ratio > 0.99), median overlap is 1.000, mean length difference is +39 characters (ours slightly longer). 34 cases have overlap below 0.5, likely caused by different boundary selection. All 1,696 both-fail cases fail with `no_header` on our side.

### Why ours captures 6,191 more cases

Kruse's two literal patterns miss header variants that are common in MIMIC-III:

| Header variant | Count |
|----------------|------:|
| `HOSPITAL COURSE:` (matched by our broader regex but not Kruse's case-sensitive literal) | 2,983 |
| `HOSPITAL COURSE` (no colon) | 1,388 |
| `SUMMARY OF HOSPITAL COURSE` | 880 |
| `Brief Hospital Course:` (case variants not matching Kruse's literal) | 181 |
| `CONCISE SUMMARY OF HOSPITAL COURSE` | 169 |
| `HISTORY OF HOSPITAL COURSE` | 160 |
| Other (NICU COURSE, hosp course, etc.) | 430 |

### Why Kruse captures 42 cases we miss

All 42 fail with `no_header` on our side. Investigation revealed two root causes, split evenly:

Half are PHI de-identification artifacts. MIMIC-III's de-identification inserts `[**...**]` tokens that break our start-of-line anchor. Two sub-patterns: (a) `[**Hospital NNN**] HOSPITAL COURSE:` where the de-identifier replaced what was likely "BRIEF" or "SUMMARY OF" with a hospital placeholder, and (b) truncated words from `[**Last Name...**]` tokens abutting the header, producing fragments like `SU[**Last Name...**]OF HOSPITAL COURSE:`. Kruse's unanchored regex matches "HOSPITAL COURSE:" anywhere in the line, bypassing these artifacts.

Half are unrecognized prefix variants not in our pattern list. Examples: "REVIEW OF HOSPITAL COURSE:", "ADDENDUM - HOSPITAL COURSE:", "MEDICAL INTENSIVE CARE UNIT HOSPITAL COURSE:", "CONTINUING HIS HOSPITAL COURSE:", "REMAINING HOSPITAL COURSE:". These are legitimate but rare headers (each appears 1-4 times in the full corpus).

All are genuine BHC sections with real clinical narrative (not false positives). This represents a 0.08% gap (42/52,726) that could be closed by either relaxing the start-of-line anchor or adding patterns for PHI artifacts and rare prefixes. The PHI artifact patterns are MIMIC-specific and would not generalize to other clinical datasets. We do not address this gap because the affected cases are too few to justify the added pattern complexity and false-positive risk.

## Known Limitations

The regex-based extraction captures everything between the BHC header and the next top-level section boundary. This works well when clinicians follow the standard discharge summary template, but some notes contain material between those two markers that is not part of the hospital course narrative. The extractor cannot distinguish this embedded content from legitimate BHC text because it lacks the headers that would trigger a boundary match.

### Embedded sub-documents

Some discharge summaries contain entire sub-documents pasted into the BHC section: admission notes, consult notes, procedure notes, or transfer summaries. Clinicians copy these into the discharge summary for reference, and the pasted content sits between the BHC header and the next section boundary without any intervening top-level header to stop extraction.

A representative example from MIMIC-IV (hadm_id REDACTED): the actual hospital course narrative occupies the first ~82% of the extracted text. The remaining ~18% consists of a signed consult note, an admission note (with its own HPI, Past Medical History, Family History, Social History, and Review of Systems sections), and additional consult entries. None of these embedded sections use headers from the end-boundary allowlist, so the extractor captures them as part of the BHC.

In MIMIC-III (hadm_id REDACTED), a similar pattern appears: the BHC contains a transfer summary with its own Past Medical History, Allergies, and Medications on Transfer sections embedded mid-narrative. The clinician documented the patient's transfer from an outside hospital by pasting the transfer note directly into the hospital course.

### Prevalence

We scanned all successful extractions for markers of embedded content: lines matching "HPI:", "History of Present Illness:", "Past Medical History:", "PMH:", "Medications:", "Current Medications:", signature lines ("Signed by ... on"), and consult/admission note headers. These are heuristic markers, not a precise count. Some matches may be legitimate BHC content (a clinician mentioning "Medications:" as a sub-header within a domain-organized course), and some embedded content may not match any of these patterns.

| Marker | MIMIC-III (51,131 successful) | MIMIC-IV (293,329 successful) |
|--------|-------------------------------|-------------------------------|
| Any marker found | 1,098 (2.2%) | 788 (0.3%) |
| Embedded meds list | 1,019 (2.00%) | 571 (0.19%) |
| Embedded PMH | 82 (0.16%) | 142 (0.05%) |
| Embedded HPI | 28 (0.05%) | 48 (0.02%) |
| Embedded signature | 1 (<0.01%) | 45 (0.02%) |
| Embedded consult | 2 (<0.01%) | 19 (0.01%) |

The most common marker is "Medications:" or "Current Medications:" appearing on its own line within the BHC. This differs from the end-boundary pattern "Medications on Discharge:". It typically represents a medications list from an embedded admission or transfer note. The overall contamination rate is low (2.2% in MIMIC-III, 0.3% in MIMIC-IV), but the affected extractions can be substantially longer than expected because the embedded content adds thousands of characters.

### Implications for downstream tasks

For BHC generation, embedded sub-documents create noise in the reference text. A model trained to generate BHCs from source notes would see these embedded admission notes and consult entries as part of the target output, potentially learning to reproduce content that a clinician would not include in a well-written hospital course. The effect is likely small given the low prevalence, but it is worth noting when interpreting length statistics or when individual extractions appear unusually long.

The contamination rate is lower in MIMIC-IV than MIMIC-III, consistent with MIMIC-IV representing more recent clinical practice where EHR systems may structure notes differently.

### Why we do not attempt to fix this

Detecting embedded sub-documents reliably would require either a much larger set of boundary patterns (with the attendant risk of false positives that truncate legitimate BHC content) or a model-based approach that understands document structure. Both options introduce more complexity and error modes than the current approach. Given the low prevalence, the pragmatic choice is to document the limitation and account for it when interpreting results, rather than risk degrading the 97-99% of extractions that are clean.

## Case review findings

52 cases were sampled across both MIMIC-III and MIMIC-IV to verify extraction correctness and the extraction-level format classification. The sample was stratified by the binary classifier's labels (16 MIMIC-III "problem-oriented," 15 MIMIC-III "chronological," 10 MIMIC-IV "problem-oriented," 10 MIMIC-IV "chronological," plus 1 hybrid format deep dive). Most were analyzed programmatically. A small subset was manually reviewed. All extraction boundaries were verified as correct.

Key findings:

**Pattern gaps identified and fixed.** "Cardiac" and "Neurologic" (without "-al") were missing from the sub-header pattern list. Both were added. Common abbreviations CV, Pulm, GU, and the term "Cardiology" remain unmatched but have not caused misclassifications because other sub-headers exceeded the threshold of 2.

**Binary classifier undercounts structured BHCs.** Roughly 9 out of 30 cases classified as "chronological" (~30%) use disease-specific problem headers (`# Upper GI bleed`, `# NSTEMI`, `1) CV:`) that are structurally domain-organized but invisible to the organ-system classifier. A separate structural analysis confirmed the actual domain-organized rate is 53% (MIMIC-III) and 64% (MIMIC-IV), compared to the binary classifier's 0.7% and 0.4%.

**Threshold of 2 validated.** One case has a single organ-system match (`#FEN:`) alongside disease-specific headers. The threshold correctly prevents a false positive. No reviewed case was misclassified due to the threshold being too high or too low.

## References

- Adams, G., Alsentzer, E., Ketenci, M., Zucker, J., & Elhadad, N. (2021). What's in a Summary? Laying the Groundwork for Advances in Hospital-Course Summarization. NAACL 2021.
- Aali, A., Van Veen, D., Arefeen, Y. I., et al. (2024). A dataset and benchmark for hospital course summarization with adapted large language models. JAMIA, 32(3), 470-479. doi:10.1093/jamia/ocae312.
- Alsentzer, E., & Kim, A. (2018). Extractive Summarization of EHR Discharge Notes. NeurIPS ML4H Workshop.
- Kruse, B., et al. (2025). Large Language Models with Temporal Reasoning for Longitudinal Clinical Summarization and Prediction. arXiv:2501.18724.
- Landes, J., et al. (2022). MedSecId: Medical Section Identification. COLING 2022.
