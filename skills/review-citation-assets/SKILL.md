---
name: review-citation-assets
description: Insert explicitly selected figures or reaction schemes into Markdown and convert stable paper-ID citations into a numbered reference list.
---

# Citation and Asset Tools

Use these deterministic tools after selecting the evidence and assets.

## Insert figures and reaction schemes

Put an explicit marker in the manuscript:

```markdown
<!-- insert:scheme-1 -->
```

Then run:

```bash
python skills/review-citation-assets/scripts/insert_assets.py \
  --input review-projects/<project_id>/manuscript.md \
  --manifest review-projects/<project_id>/assets/asset_manifest.json \
  --output review-projects/<project_id>/manuscript.with-assets.md \
  --report review-projects/<project_id>/assets/insertion_report.json
```

The tool copies each selected image beside the output manuscript and replaces
the matching marker with standard Markdown image syntax. Source-paper assets
include paper ID, source locator, reuse basis, and attribution. Confirm these
provenance fields against the source. Put the label only in `label`; captions
may omit a repeated leading “Figure 1” or “Scheme 1”. The inserter also removes
an accidental repeated leading label.

## Number citations and build references

Write stable citations in the canonical manuscript:

```markdown
The methods differ in substrate scope [@P107; @P022].
```

Then run:

```bash
python skills/review-citation-assets/scripts/merge_citations.py \
  --review-root . \
  --input review-projects/<project_id>/manuscript.with-assets.md \
  --output review-projects/<project_id>/deliverables/review.md \
  --citations-json review-projects/<project_id>/deliverables/citations.json
```

Numbering follows first appearance. Metadata comes from
`review-library/metadata/papers/`. Unknown paper IDs stop the conversion.
Incomplete, title-like, affiliation-like, or raw-LaTeX bibliographic fields are
reported as editing prompts. Repair the records used by the manuscript from
paper front matter, DOI metadata, or another reliable bibliographic source
before the final export.

Read the generated References section entry by entry. Check author names,
title, journal, year, DOI or locator, numbering from 1, and a single References
heading. Then convert the output Markdown to DOCX. Continue revising the
canonical manuscript rather than treating the numbered copy as a second
authoring branch.
