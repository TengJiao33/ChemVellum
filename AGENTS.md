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
  filling, and decide citations only after reading.
- Do not silently compress a broad topic into a mini-review. Build the source-
  figure inventory while reading, treat the first coherent manuscript as a
  synthesis draft, and make one substantive expansion pass before export.
  Scale cues are editorial diagnostics, not fixed word, page, figure, or table
  quotas.
- Never report the review as complete unless the project contains registered
  full text used in the manuscript, resolved stable paper-ID citations,
  argument-bearing visuals or a source-based reason they are unsuitable, the
  final DOCX and PDF, rendered page images, and recorded inspection of every
  page. Missing items mean the run is still in progress or blocked.
- Keep runtime papers, MinerU outputs, projects, and experiments in the
  repository's ignored local data directories. Do not commit, push, delete,
  reset, or clean them unless the user explicitly asks.
