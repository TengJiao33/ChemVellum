---
name: review-metadata-prep
description: Register MinerU or repository full text in a managed chemistry-paper library, extract bibliographic metadata, validate local paths, optionally add project-specific descriptors, and repair paths after a repository move.
---

# Review Metadata Tools

Build or extend the managed library:

```bash
python skills/review-metadata-prep/scripts/prepare_metadata.py \
  --review-root . \
  --mineru-output mineru-outputs \
  --pdf-root chem_papers \
  --discover-from-pdf-root \
  --append-registry
```

The default path extracts general bibliographic metadata and leaves optional
chemistry descriptors unconstrained. It does not assume a reaction class,
material family, analytical method, or other review topic.

Validate records and source paths:

```bash
python skills/review-metadata-prep/scripts/validate_metadata.py \
  --review-root .
```

Repair stored paths after moving the repository:

```bash
python skills/review-metadata-prep/scripts/remap_source_paths.py \
  --review-root . \
  --extract-archives \
  --write
```

Metadata extraction provides a starting record. Leave unavailable author,
title, year, journal, DOI, page, or article-number fields empty, then repair
them from the paper front matter or a reliable bibliographic record. Before
final citation numbering, inspect the records actually cited by the manuscript.
Correct author fields that contain title fragments or affiliations and titles
that contain raw LaTeX at their source records.

Structured chemistry descriptors are optional retrieval aids. Use
`--use-llm` for concise paper-supported descriptors. Supply
`--classification-rules <path>` only when the current project genuinely
benefits from a controlled vocabulary; do not require every topic to define
one.

For an existing library, refresh optional descriptors with:

```bash
python skills/review-metadata-prep/scripts/batch_llm_retag_metadata.py \
  --review-root . \
  --batch-size 3
```

Core outputs are `review-library/registry/papers.jsonl` and
`review-library/metadata/papers/<paper_id>.metadata.json`. Validation reports
describe mechanical completeness; optional descriptors do not decide whether a
paper may support a review. Stable IDs use the next available `P001`, `P002`,
and so on. Registry allocation and replacement are protected by a cross-process
file lock so simultaneous writers cannot silently reuse the same paper ID.
