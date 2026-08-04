---
name: review-topic-paper-discovery
description: Create a numbered ChemVellum review or experiment workspace, search the local library and scholarly providers for a review topic, locate lawful full text, download or import it, run MinerU when needed, and add the result to the managed local library.
---

# Topic Paper Discovery

Use this tool for the retrieval and corpus-building loop:

```text
topic question and provisional explanation -> local and external candidates
-> set-level corpus design -> lawful full text -> PDF download or repository import
-> MinerU when needed -> local library -> reading revises the explanation
-> targeted retrieval where the explanation remains uncertain
```

The model supplies the topic and decides relevance. The scripts handle provider
queries, deduplication, lawful source locations, download/import, parsing, and
library registration. Search broadly enough to assemble a useful candidate
pool. Design the initial corpus as a set before ingestion; do not turn the first
few high-ranked or easily acquired rows into the literature base by default.

## Topic input

Use a short Markdown or JSON file containing the topic, central question,
important coverage, and optional inclusion/exclusion criteria. `focused` and
`comprehensive` describe the intended scope.

## Project and experiment IDs

Create a review project before retrieval when you want to inspect or reuse its
ID explicitly:

```bash
python skills/review-topic-paper-discovery/scripts/create_project.py \
  --review-root . \
  review \
  --topic "<chemistry question>"
```

New reviews use `CVR-0001-<topic>`, `CVR-0002-<topic>`, and so on. The project
contains `project.json`, `00_discovery/`, `manuscript.md`, `assets/`,
`deliverables/`, `notes/synthesis_model.md`, `notes/`, and `runs/`. Use an
explicit `--project-id` only to continue a known project or to import a
deliberately assigned safe ID.

Allocate an isolated local experiment with:

```bash
python skills/review-topic-paper-discovery/scripts/create_project.py \
  --review-root . \
  experiment \
  --topic "<benchmark or QA purpose>"
```

Experiments use `EXP-YYYYMMDD-001-<topic>` within each UTC day. The local
registries serialize concurrent allocation and remain ignored by Git.

## Search

```bash
python skills/review-topic-paper-discovery/scripts/discover.py \
  --review-root . \
  --topic-contract-file <topic.md-or-json>
```

Discovery is deliberately long-running when the topic expands into dozens of
facet queries. Launch it once with a command timeout long enough for the whole
plan—normally 45 to 60 minutes, or longer for slow providers—or as one tracked
background job whose terminal output remains observable. The script emits
timestamped, flushed progress lines for every query and major expansion step.
While those lines continue, monitor the same process; do not terminate it at an
ordinary short-command timeout and restart from query 1. Before retrying after a
wrapper timeout, verify whether the original PID is still alive. The script
refuses a second discovery process for the same project, recovers a stale lock
after a killed process, and marks the superseded run `interrupted`. Do not reduce
the scientific query plan merely to fit a tool's default timeout.

Omitting `--project-id` allocates the next `CVR-*` project. To continue an
existing review, pass its exact ID. Before a repeated discovery run writes the
canonical `00_discovery/` directory, ChemVellum moves the previous batch to
`runs/<discovery_run_id>/discovery`; it refuses to overwrite an unidentifiable
or already archived batch.

The default external routes are Crossref, Europe PMC, and deposited-reference
expansion. Semantic Scholar and SciAtlas are optional enrichments. Provider
routes run independently, and the report records failures without cancelling
successful routes.

Discovery keeps a broad core query and adds short, topic-anchored facet queries.
Dense coverage bullets expand into several short queries so later method terms
remain searchable. By default every planned facet reaches the external
providers. `--external-query-limit` is available when provider-call control is
needed; it is not a paper-count target.

Treat the first ingested set as an orientation and working set. Read across
relevant reviews, anchor studies, and primary papers to learn the field's terms,
method families, historical links, disagreements, and meaningful comparisons.
Use that understanding to revise the project's living synthesis model and
choose the next searches in the same project. Use category labels to see where
discovery has reached, and use direct full-text support to judge whether the
literature can establish, distinguish, or bound the relationships carrying the
explanation.

Develop each consequential relationship and comparison from the direct primary
sources needed to understand its important sides, conditions, competing
interpretations, and historical change. Let the literature set settle when
targeted searches mainly return relationships and evidence already understood.
Judge explanatory change, support, and redundancy rather than aiming for a
paper count or ingesting every candidate.

For an established subject, use close reviews to navigate backward to the
primary work that established a method and forward to later papers that changed
its scope or interpretation. Discovery can reuse a relevant review found by any
provider as a deposited-reference seed.

Discovery writes a ranked local/external result set and
`external_ingest_plan.json`. It also writes `corpus_plan.draft.json`, which
groups every lawfully importable candidate with topic-contract coverage and a
provisional orientation-versus-primary role. Provider reports distinguish no
relevant results from provider failure. Use metadata and abstracts for
screening, then consult full text for material claims in the manuscript.

## Build the corpus before ingestion

Copy `00_discovery/corpus_plan.draft.json` to a project-local working corpus
plan such as `notes/corpus_plan.json`. Screen the candidates as a literature
set, then populate `selection.selected_paper_keys`, a concise
`selection_rationale`, and any genuinely deferred or uncovered axes.

Do not select papers merely because they appear first, are easiest to acquire,
or fit a convenient batch size. Assemble a coherent initial reading corpus
whose sources can orient the provisional explanation, establish or discriminate
its important relationships, reveal historical changes or competing
interpretations, and locate scope boundaries. These are ways a source can
change understanding, not roles to fill. Prefer high recall at ingestion for
lawful, clearly relevant full text because the managed library is cumulative;
reserve strict citation selection for after reading.

The corpus plan is the model's scientific screening decision, not an automatic
ranking verdict or a paper quota. If the candidate set cannot support the
declared coverage, narrow the scope or run another discovery pass before
sustained drafting. Keep unselected candidates available as a reserve for
targeted promotion when reading or writing exposes a gap.

## Acquire and ingest

Screen the corpus, then import the selected set in one call:

```bash
python skills/review-topic-paper-discovery/scripts/ingest_external_papers.py \
  --review-root . \
  --project-id <project_id> \
  --corpus-plan review-projects/<project_id>/notes/corpus_plan.json \
  --mineru-batch-size 10
```

Use repeated `--paper-key` only for a small targeted gap-fill after the initial
corpus exists. Use `--all-available` when every available plan row has already
been screened and retained. The MinerU batch size controls parsing operations;
it must not silently become the size of the scientific corpus.

The importer:

- validates downloaded PDFs;
- imports official repository JATS directly when available;
- runs MinerU for PDFs;
- retains repository-native figure assets and licence text;
- reconciles discovery metadata;
- promotes the source into the managed local library;
- records receipts so interrupted work can resume.

Receipts record the completed retrieval and ingestion actions. Assess relevance,
bibliographic accuracy, scientific support, and image reuse from the source
material.

Use `--download-only` to defer parsing. Broaden or reformulate queries, expand
references, or use another provider as reading changes the apparent structure
of the subject. Let the literature set grow when doing so could change, test, or
bound the current explanation. Give priority to a missing primary source, an
unresolved comparison, a historical change, or evidence that could distinguish
competing relationships. If the available evidence supports a narrower model,
narrow the story.

## Selection

`selected_discovery_results.json` is a cumulative candidate list. Its
`newly_ingested_paper_ids` retains every source promoted during the discovery
run, while `latest_ingested_paper_ids` records only the most recent batch.
Open the current local full text before relying on a paper;
search snippets, abstracts, earlier summaries, and remembered readings support
screening but do not replace that reading step. Ingestion makes a paper
available for reading; it does not create a reason to cite it. Cite a paper when
it establishes, tests, changes, bounds, or materially supports a relationship
discussed in the manuscript.
