---
name: chemvellum-review-e2e
description: "Run the only complete ChemVellum topic-to-review workflow, from a user-supplied chemistry topic through inspected DOCX and PDF deliverables. Use whenever the user asks to write, create, produce, run, continue, or finish a chemistry review, even if the request contains only the topic and does not mention ChemVellum, E2E, Skills, literature search, full text, MinerU, citations, figures, Word, or PDF. Do not route a new topic directly to a writing-only Skill. Coordinate numbered project creation, lawful full-text discovery and ingestion, PDF or JATS routing, managed-library registration, full-text reading, synthesis, stable citations, useful visuals, two-column export, rendered-page inspection, and final content review."
---

# Run a ChemVellum Review End to End

## Recognize the ordinary user request

A prompt such as `Write a chemistry review on <topic>` is the complete normal
entry point. The absence of workflow vocabulary narrows nothing. Load this
Skill before creating files or drafting. If another component Skill was loaded
first for such a request, return control here immediately.

Do not infer citations from model memory, create a bibliography before opening
sources, or copy an old project's prose as a shortcut. Before sustained
drafting, establish a real discovery run, acquire lawful full text, route it by
format, register it in the managed library, and open the sources that will
carry the central argument.

## Take ownership of the complete run

Treat a defined chemistry topic plus a request to write a review as authority to
complete the whole local workflow. Do not require the user to know the Skills,
name repository commands, restate the workflow, approve ordinary transitions,
or ask whether to continue after planning, discovery, a draft, citation work,
or DOCX creation.

Create or continue the correctly numbered project and keep its canonical
manuscript, assets, deliverables, and run outputs inside that project. Preserve
existing projects. Give concise progress updates while continuing to work.

Unless the user explicitly asks for a focused or mini-review, treat a broad
chemistry topic as a request for a full-length review at the scale supported by
the scientific problem and lawful evidence. Do not silently downgrade the
deliverable merely because a compact first draft already has complete headings,
resolved citations, or clean export mechanics.

Write the scholarly deliverable in English by default, regardless of the
language used for the user's request or progress conversation. This includes
the title, Abstract, keywords, headings, body, author-created figure labels,
table headings and cells, captions, Outlook, acknowledgments, and reference
annotations. User-facing progress updates may follow the user's language.
Produce a Chinese or other non-English review only when the user explicitly
requests that language. If an early draft was written in another language,
convert it to accurate academic English before substantive polishing and
preserve the chemistry, evidence strength, citations, and intended distinctions
rather than translating mechanically. Reproduced source figures may retain
their original labels; explain them in an English caption when needed.

Use a component Skill directly instead when the user explicitly asks for only
one bounded operation such as searching the library, parsing supplied PDFs,
editing an existing manuscript, repairing citations, selecting figures, or
exporting a finished Markdown file.

## Route work to the component Skills

Before using a component, read its `SKILL.md` completely and follow its source
and decision boundaries:

- `review-topic-paper-discovery` creates projects, discovers literature, and
  acquires lawful full text.
- `mineru-precise-parse-chemvellum` parses acquired PDFs. Do not send
  repository-native structured JATS through MinerU.
- `review-metadata-prep` registers parsed or imported sources in the managed
  local library.
- `review-writing-tools` owns the intellectual problem, chemical reasoning,
  comparison, evidence calibration, manuscript architecture, and prose.
- `review-source-figure-tools` exposes source visuals and their provenance for
  model judgment.
- `review-citation-assets` resolves stable paper citations and inserts selected
  assets.
- `review-export-docx` creates the default two-column DOCX, renders its PDF, and
  exposes every page for visual inspection.

This Skill owns continuation and routing, not the scientific conclusions. Do
not duplicate the component instructions here and do not create an additional
stage machine, evidence matrix, quota report, approval file, or project-local
orchestration script.

## Work in connected loops

Start with a reader question, scope, provisional central judgment, and
comparison axes. After broad discovery, use the discovery Skill's corpus-plan
workflow to choose the initial literature as a set before ingestion. Do not
build a broad review from an arbitrary first batch of paper keys, and do not
confuse a download or MinerU batch size with the intended scientific corpus.
Ingest lawful, clearly relevant full text with high recall so the cumulative
local library and reserve candidate pool can support later gap filling.

Use an initial reading set to understand the field before sustained drafting.
Read orientation reviews to learn the map, then promote and open the direct
primary studies needed across the planned comparisons, disagreements, and
boundaries. Once registered sources are available, build and browse the source-
figure inventory during reading, before sustained drafting. Reopen the retained
candidate pool or discovery whenever reading or writing exposes a missing
primary source, historical link, comparison, contradiction, mechanism, or
boundary. Reopen the manuscript structure when evidence changes the central
judgment. Choose figures, Schemes, and tables while the argument is forming,
not as decoration after the prose is finished.

For a broad full-length review, write a coherent synthesis draft and then make
one substantive expansion pass before citation assembly or export. Use that
pass to deepen the comparisons, mechanisms, representative cases, boundaries,
and visual explanations that the first draft compressed, reopening full text
and the retained candidate pool where needed. This is one whole-manuscript
editorial pass, not a new stage machine, a section-by-section gate, or an
invitation to repeat or pad prose.

Route each acquired source by format:

- Validate and parse PDFs with MinerU.
- Import official structured JATS directly, including its full text, captions,
  repository images, source locators, and licence statements.

Both routes must end in the managed library with portable metadata and
searchable local full text before the source supports substantive claims.

## Do not export an unfinished evidence product

Do not substitute manually written numbered references for stable `[@Pxxx]`
citations. Do not export while the manuscript snapshot reports zero stable
paper-ID citations, while material claims depend only on metadata, abstracts,
or search snippets, or while acquired sources have not reached the managed
library. Base consequential chemical claims on full text opened in the current
run and reopen the most direct source when an error would change the argument.
Keep abstract screening distinct from full-text reading in both the work and
the completion report; resolving a citation to a local file does not prove that
the paper was read.

For a broad comprehensive review, roughly 40 genuinely relevant cited sources
is a scale cue, not a quota. When the bibliography is much smaller, reassess the
main approaches, historical links, representative developments, comparisons,
and contrary evidence before concluding that the set is mature.

After the first mature draft of a broad review, run the writing Skill's
manuscript inspection with `--include-word-advisory`. Treat its evidence-scaled
range as a diagnostic for serious compression, never as a target or release
gate. When the draft is plainly out of proportion to the opened evidence and
declared scope, expand the science once or explicitly narrow the scope instead
of exporting a mini-review under a comprehensive title.

An empty figure inventory after source registration is a path or identity
problem to investigate, not evidence that the papers contain no usable images.
A broad manuscript with no argument-bearing source visual and only a token
table is presumptively unfinished: either integrate visuals that answer defined
reader questions or establish from the actual sources why no such visual is
appropriate. Do not add decorative figures or filler tables to satisfy a count.

## Complete the deliverable

Before export, require an evidence-bounded central judgment, sections that
advance that judgment, meaningful cross-paper comparison, calibrated chemical
claims, resolved stable citations, and selected assets already integrated into
the English manuscript. Run citation and asset insertion rather than cancelling
it and exporting the unprocessed draft.

Use the default `chemvellum_journal` layout unless the user explicitly requests
another profile. Render the DOCX to PDF immediately, open every page image, and
repair the source manuscript or export when a page exposes a problem. Mechanical
generation, complete headings, semantic correctness, citation support, and
visual acceptability are separate conclusions.

Finish by reporting the project path, main local full-text sources, manuscript,
DOCX, PDF, rendered pages, material limitations, and what was actually
inspected. Pause the run only for missing credentials, a paid or consequential
external action, unavailable lawful evidence, or a choice that would materially
change the requested topic. Otherwise continue to the inspected deliverable.

## Tell the truth about completion

Before saying that the review or E2E run is complete, verify from the filesystem
and maintained reports that all of the following are true:

- the project records at least one completed discovery run and the sources used
  for substantive claims are registered with searchable local full text;
- full-text reading and abstract-only screening are reported separately rather
  than collapsed into a single "read" count;
- the initial corpus was screened as a set rather than inherited from an
  arbitrary retrieval batch, and later evidence gaps were filled from the
  retained candidates or another discovery pass;
- the manuscript uses resolved stable paper-ID citations rather than a manually
  invented numbered bibliography;
- source-figure inventory and selection were actually considered, and useful
  argument-bearing visuals are integrated unless the opened sources establish
  why none is suitable;
- the final DOCX, PDF, and page images exist; and
- every rendered page was opened and inspected after the latest export.

These are existence and provenance checks, not fixed literature or figure
quotas. If any item is absent, the run is still in progress or is explicitly
blocked. Directory creation, a long manuscript, a reference count, successful
export, or a completed Todo list cannot substitute for these facts.
