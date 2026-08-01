---
name: review-topic-paper-discovery
description: Search the local library and scholarly providers for a review topic, locate lawful full text, download or import it, run MinerU when needed, and add the result to the managed local library.
---

# Topic Paper Discovery

Use this tool for the retrieval loop:

```text
topic query -> local and external candidates -> lawful full text
-> PDF download or repository import -> MinerU when needed -> local library
```

The model supplies the topic and decides relevance. The scripts handle provider
queries, deduplication, lawful source locations, download/import, parsing, and
library registration. Search broadly enough to assemble a useful candidate
pool, then apply close relevance judgment before ingestion.

## Topic input

Use a short Markdown or JSON file containing the topic, central question,
important coverage, and optional inclusion/exclusion criteria. `focused` and
`comprehensive` describe the intended scope.

## Search

```bash
python skills/review-topic-paper-discovery/scripts/discover.py \
  --review-root . \
  --topic-contract-file <topic.md-or-json> \
  --project-id <project_id>
```

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
Use that understanding to choose the next searches in the same project.
Use category labels to see where discovery has reached, and use direct
full-text support to judge whether the literature can carry the synthesis.

Develop each central claim and major comparison from the direct primary sources
needed to understand its important sides, variations, or historical change.
Treat the literature set as mature when targeted searches mainly return
approaches and evidence already understood. Judge support and redundancy rather
than aiming for a paper count or ingesting every candidate.

For a broad, comprehensive review, roughly 40 genuinely relevant cited sources
is a useful scale cue. If the emerging set remains well below that scale,
revisit the main approaches, comparisons, historical links, and representative
developments before concluding that it is mature. Let relevance and contribution
decide every ingestion and citation; narrower topics may settle below this scale,
while broader ones may need more.

For an established subject, use close reviews to navigate backward to the
primary work that established a method and forward to later papers that changed
its scope or interpretation. Discovery can reuse a relevant review found by any
provider as a deposited-reference seed.

Discovery writes a ranked local/external result set and
`external_ingest_plan.json`. Provider reports distinguish no relevant results
from provider failure. Use metadata and abstracts for screening, then consult
full text for material claims in the manuscript.

## Acquire and ingest

Screen the plan, then import the selected rows:

```bash
python skills/review-topic-paper-discovery/scripts/ingest_external_papers.py \
  --review-root . \
  --project-id <project_id> \
  --paper-key <paper_key> \
  --paper-key <paper_key> \
  --mineru-batch-size 10
```

Select candidates that address a named method family, a meaningful comparison,
an origin of the field, or a representative development in the review scope.
Use `--all-available` when every available plan row has already been screened
and retained.

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
of the subject. Let the literature set grow with the understanding needed for
the review. Give priority to a missing primary source, a weakly supported
comparison, a historical change, or evidence that could alter the emerging
account. If the available evidence supports a narrower story, narrow the story.

## Selection

`selected_discovery_results.json` is a candidate list. Record a short selection
reason when useful. Open the current local full text before relying on a paper;
search snippets, abstracts, earlier summaries, and remembered readings support
screening but do not replace that reading step. Ingestion makes a paper
available for reading; it does not create a reason to cite it. Cite a paper when
it contributes evidence, a comparison, a historical link, or a view discussed
in the manuscript.
