---
name: chemvellum-review-e2e
description: "Run an entire ChemVellum chemistry review from a user-supplied topic to inspected DOCX and PDF deliverables. Use whenever the user asks to write, create, produce, run, continue, or finish a chemistry review, even if they provide only the topic and do not mention E2E, Skills, literature search, full text, MinerU, citations, figures, Word, or PDF. Do not route a new topic directly to a writing-only Skill."
---

# Enter the canonical ChemVellum workflow

Before creating a project, searching, drafting, or exporting, read
`skills/chemvellum-review-e2e/SKILL.md` completely from the repository root.
Follow that canonical Skill through the inspected deliverable. Do not replace
it with a shorter plan and do not stop after writing `manuscript.md`. In
particular, use the discovery run's `corpus_plan.draft.json` to screen the
initial literature as a coverage-bearing set before the main ingestion; do not
let a convenient first batch of papers define the review's scale or scope.
Run broad discovery once with a long command timeout, normally at least 45 to
60 minutes when dozens of queries are planned. Monitor its timestamped progress
instead of terminating it at a short tool timeout or launching a duplicate
process for the same project.
Maintain the project's `notes/synthesis_model.md` as the current scientific
explanation. Let full-text reading revise its important relationships, develop
the manuscript as its reader-facing expression, and use prose, source visuals,
original high-level diagrams, and aligned tables as useful views of the same
model. Let review scale follow explanatory resolution and saturation; do not
replace that judgment with a nominal expansion pass, abstract-only screening,
a predetermined image count, or successful page rendering.
