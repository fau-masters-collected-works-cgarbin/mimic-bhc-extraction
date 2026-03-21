# mimic-bhc-extraction

Extract Brief Hospital Course (BHC) sections from MIMIC-III and MIMIC-IV discharge summaries and classify their organizational structure.

**Quick start:** [Installation](#installation) and [Usage](#usage) to run from a pip install. [Development](#development) to clone and run locally.

## Features

- **BHC Extraction**: Identifies and extracts the Brief Hospital Course section from discharge notes using pattern matching.
- **Structure Classification**: Classifies extracted BHCs into three categories:
  - **Narrative**: No labeled clinical sections.
  - **Domain-organized**: Sections labeled by clinical domain (organ system, problem, etc.).
  - **Standardized template**: NICU-style templates with fixed domain structure.
- **Modifier Detection**: Identifies acute/chronic partitions and transitional care sections.
- **Format Support**: Works with both MIMIC-III (NOTEEVENTS) and MIMIC-IV discharge note formats.
- **Quality Flags**: Includes metadata on extraction quality (short extractions, unbounded sections, etc.).
- **CLI and Python API**: Use the [command-line tool](#command-line) for batch processing or import the library for [single-note extraction](#single-note-extraction) and [programmatic batch access](#batch-extraction).

Compared to previous extraction tools, this package provides broader header pattern coverage, structure classification that connects organizational format to generation tasks, and structured extraction metadata for systematic quality assessment. See [Pattern development process](docs/extraction-design.md#pattern-development-process) for the development and validation methodology.

## Installation

```bash
pip install git+https://github.com/fau-masters-collected-works-cgarbin/mimic-bhc-extraction.git
```

## Usage

### Command-line

The code finds the BHC section in each note using [header pattern matching](docs/extraction-design.md#header-patterns), finds where it ends using an [end-boundary allowlist](docs/extraction-design.md#end-boundary-patterns), and handles [addendum deduplication](docs/extraction-design.md#addendum-deduplication) (MIMIC-III). Each output record includes extraction metadata and [structure classification](docs/extraction-design.md#structure-classification). See [Output Format](#output-format) for the full schema.

Options:

- `--input`: Path to the MIMIC notes file: `NOTEEVENTS.csv.gz` for MIMIC-III or `discharge.csv.gz` for MIMIC-IV-Note (required).
- `--output`: Path to output file (required).
- `--format`: Input format: `mimic3`, `mimic4`, or `auto` (default: auto-detect).
- `--pretty`: Write a single pretty-printed JSON array instead of JSONL.
- `--sample`: Extract from random sample of N admissions (optional - all notes are extracted if not specified).

All examples in this section use the MIMIC-III `NOTEEVENTS.csv.gz` file. The same commands work with MIMIC-IV-Note's `discharge.csv.gz` file. The extractor auto-detects the format.

```bash
# Set path to MIMIC notes file - replace with your path
export MIMIC_PATH=../mimic-iii/physionet.org/files/mimiciii/1.4/
```

Extract BHCs from a random sample of 100 MIMIC-III notes with pretty-printed JSON output (good for human inspection):

```bash
mimic-bhc-extract extract --input $MIMIC_PATH/NOTEEVENTS.csv.gz --sample 100 \
    --pretty --output bhc_sample.json
```

Extract all BHCs from a MIMIC notes file into JSON Lines format (one JSON object per line, good for programmatic use) - it will take a few minutes:

```bash
mimic-bhc-extract extract --input $MIMIC_PATH/NOTEEVENTS.csv.gz --output bhc.jsonl
```

### Python API

The package provides two levels of API: single-note functions for working with individual discharge notes, and batch extraction for processing MIMIC files end-to-end.

#### Single-note extraction

`extract_bhc()` extracts the BHC from a single discharge note string and returns a `BHCResult`. It runs the same [two-phase extraction](docs/extraction-design.md#two-phase-extraction) as the CLI but does not handle [addendum deduplication](docs/extraction-design.md#addendum-deduplication) (that is a batch concern, handled by the CLI/batch module).

```python
from mimic_bhc import extract_bhc, classify_bhc_structure

note = "Brief Hospital Course:\nPatient was admitted for chest pain..."
result = extract_bhc(note)

if result.success:
    print(result.bhc_text)         # extracted text
    print(result.bhc_char_count)   # character count (computed property)
    print(result.start_header)     # e.g. "Brief Hospital Course:"
    print(result.end_header)       # e.g. "Medications on Discharge", or None

    structure = classify_bhc_structure(result.bhc_text)
    print(structure.category)      # "narrative", "domain_organized", or "standardized_template"
```

`BHCResult.to_dict()` serializes to a dict matching the [output format](#output-format). `BHCResult.from_dict()` reconstructs from a dict (e.g. loaded from JSON output).

```python
d = result.to_dict()              # dict ready for json.dump()
loaded = BHCResult.from_dict(d)   # reconstruct from JSON
```

#### Batch extraction

`extract_from_file()` processes an entire MIMIC notes file: loads the file, filters to discharge summaries (MIMIC-III), runs extraction and structure classification on each note, and handles addendum deduplication. It returns a list of `BHCResult`, one per admission.

```python
from mimic_bhc.batch import extract_from_file

# Extract all BHCs from a MIMIC-III file
results = extract_from_file("NOTEEVENTS.csv.gz")

# Random sample with explicit format
results = extract_from_file("discharge.csv.gz", format_hint="mimic4", sample_size=100)

# Work with results directly
for r in results:
    if r.success:
        print(r.hadm_id, r.bhc_char_count, r.bhc_structure.category)
```

Parameters:

- `input_path`: Path to the MIMIC notes file (CSV, gzipped CSV, or Parquet).
- `format_hint`: `"mimic3"`, `"mimic4"`, or `"auto"` (default). Auto-detection uses the filename.
- `sample_size`: If set, extracts from a random sample of N admissions.

## Output Format

By default, output is [JSON Lines](https://jsonlines.org/) (JSONL): one JSON object per line, one per admission. Use `--pretty` for a single pretty-printed JSON array.

Each record contains:

- `hadm_id`: Hospital admission ID.
- `subject_id`: Subject ID.
- `row_id`: Note row identifier.
- `chartdate`: Date of note (if available).
- `bhc_text`: Extracted BHC text (null if extraction failed).
- `bhc_char_count`: Character count of BHC.
- `bhc_word_count`: Word count of BHC.
- `start_header`: Header that opens the BHC section.
- `end_header`: Header that ends the BHC section (next section boundary).
- `extraction_success`: Boolean success flag.
- `failure_reason`: "no_header" or "empty_section" (if failed).
- `quality_flags`: List of quality flags (e.g., "below_min_length", "unbounded", "iserror").
- `bhc_structure`: Structure classification object:
  - `category`: "narrative", "domain_organized", or "standardized_template".
  - `numbering_convention`: Convention used for labeling (e.g., "N.", "#", "standalone").
  - `modifiers`: List of structural modifiers.
  - `nicu_domain_count`: Number of NICU domains (for templates).
  - `labeled_section_count`: Number of labeled sections (for domain-organized).
- `bhc_start`: Start character offset in original note.
- `bhc_end`: End character offset in original note.
- `note_length`: Original note character count.

Extraction fails (`extraction_success: false`) when no recognizable BHC header is found in the note (`failure_reason: "no_header"`) or when the region between the header and the end boundary is empty (`failure_reason: "empty_section"`). See [Two-phase extraction](docs/extraction-design.md#two-phase-extraction) for how the extractor determines success or failure.

`iserror` is a MIMIC-III-specific quality flag, not a failure condition. MIMIC-III's NOTEEVENTS table includes an ISERROR column that flags notes entered in error (e.g., filed to the wrong patient). The extractor still attempts extraction on these notes and can succeed if they contain recognizable headers and content. The `iserror` flag is included in `quality_flags` so consumers can filter them out if desired.

## Supporting Tools

### Extraction statistics

The `stats` command reads extraction output (JSON or JSONL from the `extract` command) and prints a summary: success/failure counts, header and end-header distributions, BHC length statistics, and structure classification distribution.

```bash
uv run mimic-bhc-extract stats --input bhc.jsonl
```

### Discovering section headers

The `discover` command scans discharge notes for lines that look like section headers and returns a frequency table. Each match is labeled with its role: "BHC header" (matched by current start patterns), "End boundary" (matched by current end-boundary patterns), or "Other" (not currently recognized as either). This helps identify rare header variants that might be worth adding to the patterns.

```bash
# Simplify the command - replace with your path
export MIMIC_PATH=../mimic-iii/physionet.org/files/mimiciii/1.4/

# Omit the --notes flag to use the default (500 notes), or set to 0 to scan all notes
uv run mimic-bhc-extract discover --input $MIMIC_PATH/NOTEEVENTS.csv.gz --notes 100
```

While adding more variants to the patterns can increase extraction coverage, it also risks false positives if the pattern becomes too permissive. The discovery tool helps balance this tradeoff by showing what headers exist in the notes and how they are currently classified.

See [Pattern development process](docs/extraction-design.md#pattern-development-process) for how this fits into the workflow for extending or validating patterns.

## MIMIC Format Support

### MIMIC-III

- Input: `NOTEEVENTS.csv.gz` (PhysioNet MIMIC-III v1.4). Expected columns: HADM_ID, SUBJECT_ID, ROW_ID, TEXT, CATEGORY, CHARTDATE, ISERROR.
- Filters to CATEGORY = 'Discharge summary'.
- Handles multiple notes per admission (addendums). Keeps the one with the longest BHC.

### MIMIC-IV

- Input: `discharge.csv.gz` (PhysioNet MIMIC-IV-Note v2.2). Expected columns: hadm_id, subject_id, note_id, text, charttime.
- No filtering needed (discharge table contains only discharge summaries).
- One row per admission.

## Design Principles

- **No filtering**: All records appear in output with metadata (including failed extractions). Consumers decide what to filter.
- **Single data structure**: `BHCResult` is the canonical representation throughout. It is populated in layers: extraction (`extract_bhc` fills text, headers, offsets), enrichment (callers add metadata and structure classification via `dataclasses.replace()`), and serialization (`to_dict()` at the output boundary). See [BHCResult as the single data structure](docs/extraction-design.md#bhcresult-as-the-single-data-structure) for the full design.
- **Core logic is reusable**: `extract_bhc()` and `classify_bhc_structure()` depend only on the standard library and operate on a single note string. The batch module adds MIMIC-specific concerns (file loading, discharge summary filtering, addendum deduplication) on top. DuckDB is used internally for efficient file loading but is not exposed to consumers.

For a detailed walkthrough of the extraction algorithm, pattern design, and deduplication logic, see [docs/extraction-design.md](docs/extraction-design.md). For extraction results on MIMIC-III and MIMIC-IV, tool comparisons, and known limitations, see [docs/empirical-findings.md](docs/empirical-findings.md).

## Development

Requires Python >= 3.12 and [uv](https://docs.astral.sh/uv/).

### Setup

```bash
uv sync
uv run pre-commit install
```

This installs all dependencies (including dev/test) and enables pre-commit hooks that run ruff on every commit (lint + format).

### Running locally

This section has the local equivalent of [usage](#usage) examples.

All examples in this section use the MIMIC-III `NOTEEVENTS.csv.gz` file. The same commands work with MIMIC-IV-Note's `discharge.csv.gz` file. The extractor auto-detects the format.

After setup, use `uv run` to invoke the CLI within the project environment.

Try a small sample to verify it works:

```bash
# Simplify the command - replace with your path
export MIMIC_PATH=../mimic-iii/physionet.org/files/mimiciii/1.4/
uv run mimic-bhc-extract extract --input $MIMIC_PATH/NOTEEVENTS.csv.gz --sample 100 \
    --pretty --output bhc_sample.json
```

Once you verify it works, run on the full file (it will take a few minutes):

```bash
uv run mimic-bhc-extract extract --input $MIMIC_PATH/NOTEEVENTS.csv.gz --output bhc.jsonl
```

### Running tests

```bash
uv run pytest
```

### Linting and formatting

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

These run automatically on commit via pre-commit hooks, but can be run manually to check before committing.
