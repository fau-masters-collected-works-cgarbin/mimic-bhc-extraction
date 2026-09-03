# BHC format coding rules

Rules for classifying a Brief Hospital Course by organizational format. `src/mimic_bhc/structure.py` implements the same rules in code, and comparing hand-coded cases against it is how `classify_bhc_structure()` is validated.

To use it: read a BHC, apply rule 1 and rule 2, and record the fields listed at the end. Do not read `structure.py` while coding, because you would copy its answers instead of applying the rules.

The examples are invented for this document.

## Categories

**Narrative.** Continuous prose that follows the clinical trajectory, with events in the order they happened. Topics may shift, but the wording alone signals the shift.

**Domain-organized.** Content divided into labeled sections, one per clinical problem or organ system.

A neonatal template, whose section list is fixed by the form, counts as domain-organized.

## Rule 1: category

Assign **domain-organized** when the BHC divides its content into **two or more** explicitly labeled sections. Assign **narrative** otherwise.

Stop counting at two. Whether a third exists changes nothing.

A labeled section is a line that opens a new topic and carries a visible marker. The markers in this corpus are:

- A hash prefix
- A number, in any style
- An asterisk
- A short capitalized label followed by a colon, with text after it on the same line

These do not count as labeled sections:

- Discharge-summary headers that leaked into the extracted text. They are extraction artifacts: text from the surrounding sections of the discharge summary.
- A lone labeled section. One header-like line in otherwise continuous prose is more often an artifact than a decision about organization.

Domain-organized, two labeled sections:

```text
# Pneumonia
Treated with ceftriaxone, afebrile by day three.

# Anemia
Hemoglobin stable, no transfusion required.
```

Narrative, because the only markers are discharge-summary headers that leaked in:

```text
She was admitted with pneumonia and improved on ceftriaxone over four days.

Discharge Medications: Amoxicillin 500 mg three times daily.
Discharge Condition: Stable.
```

## Rule 2: acute/chronic partition

Present when the BHC carries **at least one** header labeling a group of problems by acuity. Five words do that labeling, and any one of them is enough:

- Acute
- Active
- Chronic
- Stable
- Inactive

The name of this rule is shorthand for that list. `ACTIVE ISSUES` counts, and so does `INACTIVE ISSUES`. Do not read the rule as acute against chronic only, which is the mistake the name invites.

The header must stand on its own line, with nothing after it but an underline or bold marker. That is the reverse of rule 1's colon marker, which needs text on the same line. The same words inside a sentence count for neither rule.

One labeled group is enough. A BHC that runs prose and then labels a chronic section is still splitting acute from chronic. The reader infers the unlabeled half.

Present, one labeled group:

```text
ACUTE ISSUES:
# Pneumonia
Treated with ceftriaxone.

# Anemia
Hemoglobin stable, no transfusion required.
```

Absent, the words appear inside a sentence:

```text
Chronic problems were managed per the outpatient regimen.
```

## Transitional Issues

Present when a labeled section near the end of the BHC collects follow-up instructions, pending results, medication changes, or handoff items.

The coder records this field. It does not count toward the agreement score.

## Ambiguous cases

Every case gets a label. When a case sits on the boundary and the rule does not settle it, pick the closer reading and write a note saying what made it hard.

Do not set ambiguous cases aside. Dropping the hard ones would raise the agreement rate by removing exactly the cases that test the rule.

## What to record

Per case:

- Category
- Acute/chronic partition, yes or no
- Transitional Issues section, yes or no
- A note, if the rules did not settle the case
