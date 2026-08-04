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
manuscript, living synthesis model, assets, deliverables, and run outputs inside
that project. Preserve existing projects. Give concise progress updates while
continuing to work.

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
  acquires lawful full text. Treat its broad external discovery as a tracked
  long-running command: allow a suitable timeout, monitor its flushed progress,
  and never start a second process for the same project while the first lives.
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
orchestration script. `notes/synthesis_model.md` is the one living scientific
account shared by reading, writing, and visual reasoning; it is not a checklist,
completion record, or parallel manuscript.

## Work in connected loops

Start with a reader question, scope, and provisional explanation in
`notes/synthesis_model.md`. Express the causal, conditional, comparative, and
competing relationships that currently organize the answer. Keep this model
revisable: it represents the best current scientific understanding, not an
outline to fill.

After broad discovery, use the discovery Skill's corpus-plan workflow to choose
the initial literature as a set before ingestion. Prefer sources that can
orient, establish, distinguish, revise, or bound an important relationship in
the model. Do not build a broad review from an arbitrary first batch of paper
keys, and do not confuse a download or MinerU batch size with the intended
scientific corpus. Ingest lawful, clearly relevant full text with high recall
so the cumulative local library and reserve candidate pool can support later
questions.

Read orientation reviews to learn the field, then open the direct primary
studies that test the relationships carrying the central explanation. When a
source changes an explanation, its conditions, or its boundary, update the
synthesis model and reconsider the manuscript structure. When it merely repeats
an already understood relationship, use it only when the repetition itself
matters. Reopen the retained candidate pool or discovery when an uncertainty
could materially change the model. Let targeted reading settle when additional
sources mainly reinforce relationships already understood.

Develop the manuscript continuously as the reader-facing expression of the
synthesis model. Give a relationship enough space to establish the observation,
discriminating comparison, chemical explanation, boundary, and implication that
matter; compress genuinely redundant examples. A broad review becomes long
because its explanation contains consequential distinctions and interactions,
not because a later pass pads a compact draft. If the declared title promises
relationships that the evidence cannot resolve, narrow the claim or scope.

Build and browse the source-figure inventory during this same loop. Use prose,
a source visual, an original high-level diagram, or an aligned table according
to which form most clearly exposes a relationship in the model. Choose these
views while the explanation is forming, not as decoration after the prose is
finished and not to reach a count.

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

Do not use bibliography size, word count, page count, paragraph count, figure
count, or table count to decide scientific maturity. Use the writing Skill's
manuscript inspection for mechanical observations, not as the engine of scale.
Judge maturity by explanatory saturation: further targeted reading no longer
changes the important relationships, competing explanations are distinguished
as far as the evidence permits, and the manuscript preserves the conditions and
boundaries needed for the reader to reconstruct the central explanation.

An empty figure inventory after source registration is a path or identity
problem to investigate, not evidence that the papers contain no usable images.
If the synthesis model contains a mechanism, comparison, time course, decision
space, or boundary that prose would make the reader reconstruct, express it in
the useful visual form supported by the sources. Do not add decorative figures
or filler tables.

## Complete the deliverable

Before export, require an evidence-bounded central judgment, sections that
advance that judgment, meaningful cross-paper comparison, calibrated chemical
claims, resolved stable citations, and a manuscript whose prose and visuals
faithfully express the current synthesis model. Run citation and asset insertion
rather than cancelling it and exporting the unprocessed draft.

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
- the living synthesis model records the current explanation and changed as
  consequential evidence revised its relationships, conditions, or boundaries;
- the manuscript uses resolved stable paper-ID citations rather than a manually
  invented numbered bibliography;
- source-figure inventory and selection were considered as alternative views of
  the same explanation, and the chosen prose, figures, Schemes, and tables are
  consistent with that model;
- the final DOCX, PDF, and page images exist; and
- every rendered page was opened and inspected after the latest export.

These are existence, provenance, and coherence checks, not fixed literature or
figure quotas. If any item is absent, the run is still in progress or is
explicitly blocked. Directory creation, a long manuscript, a reference count,
successful export, or a completed Todo list cannot substitute for these facts.
