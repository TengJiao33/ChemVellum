---
name: review-writing-tools
description: "Develop, revise, or assess the intellectual and chemical quality of an existing chemistry review after managed full-text evidence is being read or a manuscript already exists. Use for a bounded writing, synthesis, comparison, or manuscript-review task. Never use this Skill to start from a new topic or to satisfy a request to write, create, produce, run, continue, or finish a complete review; route those requests to chemvellum-review-e2e. This Skill does not own project creation, discovery, ingestion, citation assembly, export, or the end-to-end run."
---

# Think and Write a Chemistry Review

## Redirect complete-review requests

If the user supplied a topic and expects a review to be written or completed,
stop before creating files or drafting. Read
`skills/chemvellum-review-e2e/SKILL.md` completely and continue under that
Skill. Do this even if this writing Skill was selected automatically. A topic,
an empty project, or an empty local library is not an existing evidence base.

Do not create a manual numbered bibliography from model memory. Do not treat
the ability to produce fluent prose as evidence that the literature was found,
opened, or understood.

Write one canonical Markdown manuscript. Maintain the project's
`notes/synthesis_model.md` as the living explanation behind it; keep other
optional notes and helper outputs inside the review project.

Treat this skill as guidance for judgment, not a form to complete. Do not emit
planning matrices, paragraph checks, compliance reports, or proof files unless
the user asks for them. The synthesis model is not another deliverable or a
checklist: revise it only when scientific understanding changes. Think
continuously while reading and drafting. When a consequential claim needs
stronger evidence or a different frame, interrupt the drafting to retrieve or
reassess evidence.

## Find the intellectual problem

A topic names a body of literature. A review needs a problem that makes that
literature worth reorganizing for a reader. Look for a live tension such as:

- an accepted explanation that no longer fits important observations;
- results that diverge for reasons hidden by the usual taxonomy;
- a laboratory literature that does not answer the real operating problem;
- several methods whose apparent ranking changes with the comparison basis;
- a mechanism, material property, device response, and practical outcome that
  have not yet been connected across scales;
- a new capability whose design rules or limits remain obscure.

State the resulting reader question and a provisional answer. The answer is the
review's central judgment: specific enough to organize evidence, but revisable
when full-text reading reveals a stronger explanation or an unresolved branch.
Define the material, time, application, and evidentiary boundaries that make the
question answerable. Explain why the chosen organization exposes the problem
better than chronology or a familiar category list.

Let the title, Abstract, and Introduction express the same intellectual center
at different resolutions. The Abstract should give the central judgment and
its most important boundary or unresolved link, not merely preview sections.

## Choose an architecture that explains

Do not impose one outline on every topic. Select the architecture that lets the
literature change the reader's understanding. Useful possibilities include:

- a causal chain from composition or intervention through structure and
  mechanism to performance;
- coupled mechanism, measurement, catalyst or material design, and reactor or
  device behavior across scales;
- a disputed claim organized around what each technique can establish, its
  confounders, and the controls that discriminate among explanations;
- a decision map that first establishes method families, then compares them on
  common criteria, then tests feasibility under realistic constraints;
- a narrow exception organized around competing explanations for why a common
  assumption fails;
- a synthetic-method review organized by the variable that actually controls
  reactivity or selectivity, rather than by a list of named reactions.

These are reasoning shapes, not required headings. A broad review may change
axes when the reader's question changes—for example, from reaction pathways to
decision metrics and then to scale constraints. Make the transition explicit.
Each section should inherit a question from the central problem and leave the
reader with a changed or more qualified answer that the next section uses.

## Maintain one explanatory model

Use `notes/synthesis_model.md` to state the best current answer to the reader
question and the relationships that make that answer true, conditional, or
uncertain. Relationships may be causal, mechanistic, comparative, temporal,
scale-dependent, or competing. Attach papers to relationships as evidence; do
not organize the model as a paper list.

When full-text evidence establishes, distinguishes, revises, or bounds an
important relationship, revise the model and let that change propagate into
the architecture, prose, and visuals. When another paper merely repeats an
already understood relationship, use the repetition only when its independent
support, scope, or representativeness matters. Keep high-leverage uncertainties
visible when resolving them could change the central judgment or an important
boundary.

Develop the manuscript continuously as a reader-facing expression of this
model. Give each consequential relationship the resolution needed to explain
the observation, aligned evidence, chemical reason, limiting condition, and
implication that actually matter. Compress redundant examples. This makes a
broad review grow when the scientific explanation is rich while allowing a
narrow question to remain concise; there is no separate expansion stage.

## Compare before concluding

Bring together papers that answer, extend, or contest the same question. Before
ranking or reconciling them, align the basis that could change the conclusion:
the chemical identity and state of the system, preparation and operating
conditions, substrate or feed composition, concentration and time, measurement
endpoint, normalization or denominator, and relevant spatial scale. Choose only
the dimensions that matter to the review question.

If decisive conditions cannot be aligned, do not force a ranking. Explain why
the results are not directly comparable and identify the hidden variable or
missing measurement. Incomparability is often a substantive finding.

Search for both invariants and contrasts. Ask what remains true across systems,
what changes, and what chemical or physical difference could account for the
change. Use counterexamples and negative results to locate the boundary of an
explanation. Preserve competing mechanisms when available evidence does not
locate the branch point.

Let paragraphs carry reasoning rather than chronology. A strong paragraph
usually moves from a shared judgment to aligned evidence or a discriminating
contrast, then to the chemical explanation and its boundary or implication.
Vary that movement naturally; it is not a sentence template.

Catalog prose says that one paper reported a result, another later reported a
second result, and a third extended it. Synthesis asks what those results mean
together. For example, three reported yields should not become a ranking when
substrate class, concentration, and endpoint differ; the paragraph should state
what can be compared, what cannot, and which condition plausibly explains the
difference. If removing author names leaves a paragraph with no claim, rewrite
around the common question while retaining the studies as evidence.

## Calibrate chemical claims to evidence

Keep observation, discriminating control, calculation or model, source-author
interpretation, review-level inference, and future hypothesis distinct. Express
the distinction through accurate verbs and claim strength rather than repeatedly
labeling evidence levels. A spectrum can be consistent with an assignment; it
does not by itself prove a complete mechanism. A fitted component is not a
chemical species until the assignment survives relevant controls and confounds.

For consequential or fragile chemical statements, reopen the most direct source
and verify the system identity, reagent and catalyst identity, oxidation or
charge state, conditions, substrate and product scope, selectivity basis,
stereochemical source, proposed active state, characterization inference, and
the citation attached to the claim. Concentrate this effort where an error would
change the argument; do not stop after every paragraph to produce a check record.

Use reviews to learn vocabulary, history, and neighboring approaches. Follow
their references to primary studies for specific experimental, measured,
mechanistic, or computational claims, and follow later citations when the field
has revised the original picture. Base substantive claims on full text opened in
the current run. When evidence is thin, narrow the claim or retrieve more
evidence.

## Derive principles without outrunning the literature

A useful design principle names a controllable variable, the causal or strongly
supported chain by which it changes an outcome, and the conditions under which
the relation breaks. Derive it from repeated aligned evidence, a discriminating
contrast, or a mechanism supported by appropriate controls. Do not promote a
frequent correlation, a single best-performing example, or an attractive
application into a universal rule.

When the literature supports only a boundary, say so. Knowing that a metric is
confounded, a catalyst state is condition-dependent, or an optimum shifts with
feed composition can be more useful than inventing a broad rule.

## Make visuals perform synthesis

Build and browse the available source-figure inventory while reading, before
sustained drafting. Treat prose, figures, Schemes, and tables as alternative
views of the same synthesis model. Choose the view while reasoning, not after
the prose is finished or by deciding a count in advance. A visual should perform
an intellectual operation that prose would make the reader reconstruct, such
as:

- exposing the review's organizing axis or causal chain;
- aligning mechanisms, conditions, or outcomes across studies;
- showing where a disputed assignment fails or which control discriminates;
- connecting molecular, mesoscale, device, reactor, or system behavior;
- mapping a decision space or a well-supported design boundary.

Use a source visual for concrete evidence. Use a table when entries share a
meaningful comparison basis and the aligned conditions are visible. Use an
original integrative visual only when labels, relationships, and citations can
be verified; do not redraw detailed chemical structures or reaction Schemes.
Once a visual has earned its place for a clear reader question, integrate it
into the argument unless later evidence makes it unsuitable; do not demote every
selected visual to optional export decoration.
Do not set a figure or table quota. A visual earns its place by compressing an
important relationship, not by satisfying a density target.

## Let the Outlook follow from the argument

Recover the strongest judgments established in the body, then identify the
missing link that prevents the next explanation, comparison, or design choice.
State what evidence, control, model, material change, or scale-aware experiment
would resolve it and why. Give priority to gaps the review has demonstrated.
Avoid generic lists of artificial intelligence, operando characterization,
scale-up, or sustainability unless the body showed the exact problem each would
solve.

## Grow the evidence base responsively

Let the topic and emerging argument shape the literature set. Use an initial
reading set to establish vocabulary, main approaches, and a useful provisional
explanation, then let changes and uncertainties in the synthesis model expose
targeted search questions. A missing historical link, unresolved disagreement,
unsupported comparison, or relationship carried mainly by an orientation
review justifies further search. Let the set settle when new targeted searches
mostly repeat relationships and evidence already understood. A paper belongs
because it changes, tests, bounds, or materially supports the explanation.
State material coverage limits briefly.

## Available tools

Search managed metadata and full text:

```bash
python skills/review-writing-tools/scripts/search_library.py \
  --review-root . \
  --query "reaction mechanism selectivity limitation"
```

Inspect a manuscript without gating it:

```bash
python skills/review-writing-tools/scripts/inspect_review.py \
  --review-root . \
  --input review-projects/<project_id>/manuscript.md \
  --profile comprehensive \
  --output review-projects/<project_id>/review_snapshot.json
```

The snapshot reports substantive word count, stable citations and local
full-text resolution, image paths, Markdown tables, missing Abstract or legacy
chemistry markup, and suspicious cited metadata. It returns a non-zero exit code
only for broken local mechanics such as an unknown stable paper ID or a missing
local image. Length, visual density, table use, balance, and synthesis remain
editorial observations.

Prefer the canonical stable-ID manuscript for this inspection. A numbered
`deliverables/review.md` is also supported when its sibling `citations.json`
identifies that exact output; the inspector recovers the underlying paper IDs
from that deterministic projection instead of treating numbered references as
an empty evidence base.

The default snapshot omits a word target. Add `--include-word-advisory` only
when the user explicitly asks for a numerical length reference. The advisory is
descriptive and must not drive reading, writing, visual selection, maturity, or
release decisions. Judge scientific scale from the explanatory model and the
resolution needed to communicate it without losing consequential relationships.

Use tools to locate, copy, number, format, browse, and render material. Keep
editorial judgment with the writer. When a tool has a defect, record the issue
and make a transparent manual edit.
