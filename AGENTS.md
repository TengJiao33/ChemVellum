# ChemVellum agent entry point

ChemVellum turns a user-supplied chemistry topic into an evidence-led review
with inspected DOCX and PDF deliverables.

- When a user supplies a chemistry topic and asks to write, create, produce,
  run, continue, or finish a review, first read
  `skills/chemvellum-review-e2e/SKILL.md` completely and follow it. This rule
  applies even when the user does not mention ChemVellum, E2E, Skills, tools,
  full text, MinerU, citations, figures, Word, or PDF.
- Do not route a topic-to-review request directly to `review-writing-tools`.
  That Skill is a component for scientific synthesis after evidence is being
  read or for revising an existing manuscript.
- A normal topic-to-review request authorizes the complete local workflow.
  Continue through discovery, lawful full-text acquisition, PDF/JATS routing,
  registration, reading, writing, figures, stable citations, DOCX/PDF export,
  and inspection of every rendered page. Do not stop at a plan or draft.
- Never write substantive review claims from model memory and then decorate
  them with manually invented numbered references. Search results, abstracts,
  and metadata are screening aids, not proof that a paper was read.
- After discovery, construct a coverage-driven corpus with
  `00_discovery/corpus_plan.draft.json` before the main ingestion. Do not let a
  convenient first batch of paper keys define the review. Ingest lawful,
  clearly relevant full text broadly, retain the remaining candidates for gap
  filling, and decide citations only after reading. Maintain the project's
  `notes/synthesis_model.md` as the current explanation: select and reopen
  sources when they can establish, distinguish, revise, or bound relationships
  that matter to the reader question.
- Do not silently compress a broad topic into a mini-review. Develop the
  manuscript continuously as the reader-facing expression of the synthesis
  model, and use prose, source visuals, original high-level diagrams, and tables
  as appropriate views of that same explanation. Let scale follow explanatory
  resolution and saturation, not fixed word, page, source, figure, or table
  targets and not a nominal expansion pass.
- Never report the review as complete unless the project contains registered
  full text used in the manuscript, resolved stable paper-ID citations,
  a synthesis model consistent with the manuscript and its useful visual views,
  the final DOCX and PDF, rendered page images, and recorded inspection of every
  page. Missing items mean the run is still in progress or blocked.
- Keep runtime papers, MinerU outputs, projects, and experiments in the
  repository's ignored local data directories. Do not commit, push, delete,
  reset, or clean them unless the user explicitly asks.
