# ChemVellum

ChemVellum is an agent-facing system for researching, writing, illustrating,
citing, and exporting evidence-based chemistry reviews. Its core is deliberately
small: **8 Skills and 20 deterministic Python scripts**. The model reads papers,
forms the review's central judgment, compares the chemistry, and writes the
manuscript; scripts handle retrieval, storage, parsing, citation bookkeeping,
asset placement, export, and rendering.

The repository is self-contained. Runtime paths are resolved from the checkout
root rather than from a parent workspace. A fresh clone includes the expected
storage directories but not private PDFs, generated projects, API credentials,
or large experiment archives.

## What is included

| Skill | Scripts | Responsibility |
|---|---:|---|
| `chemvellum-review-e2e` | 0 | Own a topic-to-deliverable run, route work across the component Skills, and continue through inspected DOCX/PDF output. |
| `review-topic-paper-discovery` | 4 | Create numbered projects, plan broad searches, search local and external sources, and ingest lawful full text. |
| `mineru-precise-parse-chemvellum` | 1 | Parse local PDFs with MinerU while preserving Markdown, images, JSON, and archives. |
| `review-metadata-prep` | 7 | Register papers, maintain portable metadata, validate records, and repair paths after relocation. |
| `review-writing-tools` | 2 | Develop the intellectual argument, search the managed library, and inspect a manuscript without turning observations into gates. |
| `review-source-figure-tools` | 1 | Inventory source figures, Schemes, and tables for human or model judgment. |
| `review-citation-assets` | 2 | Insert selected assets and generate stable citations and references. |
| `review-export-docx` | 3 | Export Markdown to DOCX, audit document structure, render PDF, and produce page images. |

The normal topic-to-deliverable entry point is
[`skills/chemvellum-review-e2e/SKILL.md`](skills/chemvellum-review-e2e/SKILL.md).
Agents that support `AGENTS.md` load the repository-level routing rule
automatically. Qoder also discovers the thin project adapter in
`.qoder/skills/chemvellum-review-e2e/`; both point to the same canonical Skill,
so a new user can provide only a chemistry topic and ask for a review.
The detailed writing philosophy is in
[`skills/review-writing-tools/SKILL.md`](skills/review-writing-tools/SKILL.md).
The compact end-to-end guide is in
[`skills/技能工作流说明.md`](skills/技能工作流说明.md).

## Repository layout

```text
ChemVellum/
├─ AGENTS.md                       # platform-neutral topic-to-E2E routing
├─ .qoder/skills/                  # thin Qoder adapter to the canonical E2E Skill
├─ skills/                         # 8 agent Skills and 20 Python scripts
├─ tests/                          # maintained unittest suite
├─ view/                           # optional local artifact browser
├─ template/                       # export/reference templates
├─ review-library/                 # managed metadata and registry
├─ chem_papers/                    # local source PDFs; ignored by Git
├─ mineru-outputs/
│  ├─ markdown/                    # normalized full text; ignored by Git
│  ├─ extracted/                   # figures and structured parse output
│  ├─ raw_zips/                    # original MinerU result archives
│  └─ runs/                        # one immutable manifest per parse invocation
├─ review-projects/                # CVR-0001-topic, CVR-0002-topic, ...
└─ workspace/experiments/          # EXP-YYYYMMDD-001-topic, ...
```

The ignored runtime directories are intentionally part of the layout through
tracked placeholders. Cloning the repository does not download the maintainer's
paper library or generated demonstrations.

## Requirements

- Python 3.11 or later
- packages in `requirements.txt`
- network access for external discovery and acquisition
- a MinerU API token only when parsing PDFs through MinerU
- Microsoft Word on Windows for real DOCX-to-PDF conversion
- Poppler or MiKTeX `pdftoppm` for rendered page images

Create an environment and install the Python dependencies:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

macOS or Linux:

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add only the services you use. Do not commit
`.env` or API tokens.

## Verify a fresh clone

Run the maintained test entry point from the repository root:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

Then check that the local library command can run:

```bash
python skills/review-writing-tools/scripts/search_library.py --review-root . --query "reaction mechanism selectivity limitation"
```

An empty result is normal before papers have been added.

## Start a review

For normal use, open the ChemVellum repository root and give the agent the
topic directly, for example: `Write a chemistry review on mechanochemical
synthesis of covalent organic frameworks.` The `chemvellum-review-e2e` Skill
owns the complete run through lawful full-text acquisition, reading, synthesis,
stable citations, useful visuals, the default two-column DOCX/PDF export, and
rendered-page inspection. The scholarly manuscript and deliverables are written
in English by default even when the request and progress conversation use
another language; another manuscript language must be requested explicitly.
For a broad topic, the default product is a full-length review rather than a
mini-review. The project maintains one living synthesis model: full-text
reading revises its important relationships, the manuscript explains those
relationships to the reader, and figures, Schemes, and tables provide useful
alternative views of the same explanation. Scale follows the resolution and
saturation of that model, not a fixed word, page, source, figure, or table
target and not a nominal expansion pass.
The commands below document the same workflow for
manual recovery or inspection; they are not extra setup that a normal user must
perform.

1. Define a topic, the reader's central question, scope boundaries, and the
   comparisons that would change the answer.
2. Create the next numbered project:

   ```bash
   python skills/review-topic-paper-discovery/scripts/create_project.py --review-root . review --topic "your chemistry question"
   ```

   The allocator creates `CVR-0001-<topic>`, then increments the numeric prefix
   from existing local projects. It also seeds `notes/synthesis_model.md`, the
   living scientific explanation shared by reading, writing, and visual
   reasoning. Pass the project ID to discovery. Alternatively, omit
   `--project-id` when calling `discover.py` and discovery will allocate the
   project itself.
3. Use `review-topic-paper-discovery` to search broadly. Discovery writes
   `00_discovery/corpus_plan.draft.json`, where importable candidates are shown
   together with provisional scope coverage and literature-role hints. Screen
   the initial reading corpus as a set, record its selected paper keys and
   rationale in a project-local corpus plan, and keep the remaining candidates
   available for later gap filling. Do not let the first few ranked or easiest
   downloads define the review. Re-running discovery for the same explicit
   project moves the prior `00_discovery` batch into
   `runs/<discovery_run_id>/discovery` before writing the new batch.
4. Import the complete selected corpus through the lawful repository/PDF path
   described by the discovery Skill. Directly obtained PDFs may be placed under
   `chem_papers/`.
5. Parse PDFs when necessary:

   ```bash
   python skills/mineru-precise-parse-chemvellum/scripts/parse_chemvellum_pdfs.py --input-dir chem_papers
   ```

6. Register or refresh the managed library:

   ```bash
   python skills/review-metadata-prep/scripts/prepare_metadata.py --review-root . --mineru-output mineru-outputs --pdf-root chem_papers --discover-from-pdf-root --append-registry
   ```

7. Maintain the current explanation at
   `review-projects/<project_id>/notes/synthesis_model.md` and the canonical
   reader-facing manuscript at `review-projects/<project_id>/manuscript.md`.
   Let consequential changes or uncertainties in the explanation reveal
   targeted follow-up searches; compress evidence that only repeats an already
   understood relationship.
8. Resolve stable citations and selected assets before export. The default
   export is the branded two-column `chemvellum_journal` layout. Inspect every
   final PDF page rather than treating successful export as editorial
   acceptance.

Each Skill contains its own commands and decision boundary. ChemVellum does not
require a stage machine, evidence matrix, fixed source count, figure quota, or
paragraph-by-paragraph compliance report. The synthesis model is a revisable
scientific account, not another approval or completion file.

For an isolated benchmark or QA attempt, allocate a local experiment directory:

```bash
python skills/review-topic-paper-discovery/scripts/create_project.py --review-root . experiment --topic "retrieval smoke test"
```

Project, experiment, paper, discovery-run, and MinerU-run identifiers have
different namespaces: `CVR-*`, `EXP-*`, `P*`, and timestamped run IDs. Their
registries and runtime contents remain ignored by Git, so a fresh clone starts
empty while a working checkout accumulates its own library and history.

## Moving an existing working library

Copy the whole project directory, including the ignored runtime data, then run:

```bash
python skills/review-metadata-prep/scripts/remap_source_paths.py --review-root . --extract-archives --write
```

Managed paths are stored relative to the repository whenever the source is
inside the checkout. External files remain absolute and should be copied into
the standard storage layout when a fully portable project is required.

## Optional local browser

```bash
python view/serve_review_dashboard.py --review-root . --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`. The browser exposes local artifacts; it does not
replace source reading or visual QA.

## Data and publication boundary

- Keep copyrighted PDFs, extracted full text, generated manuscripts, rendered
  pages, experiment archives, and credentials out of Git unless their rights and
  intended distribution are explicit.
- Keep source identity, figure provenance, license evidence, and stable citation
  IDs with the review project.
- Treat script reports as mechanical observations. Scientific support,
  synthesis quality, lawful reuse, and page appearance remain editorial
  judgments.
