# ChemVellum

ChemVellum is an agent-facing system for researching, writing, illustrating,
citing, and exporting evidence-based chemistry reviews. Its core is deliberately
small: **7 Skills and 19 deterministic Python scripts**. The model reads papers,
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
| `review-topic-paper-discovery` | 3 | Plan broad searches, search local and external sources, and ingest lawful full text. |
| `mineru-precise-parse-chemvellum` | 1 | Parse local PDFs with MinerU while preserving Markdown, images, JSON, and archives. |
| `review-metadata-prep` | 7 | Register papers, maintain portable metadata, validate records, and repair paths after relocation. |
| `review-writing-tools` | 2 | Develop the intellectual argument, search the managed library, and inspect a manuscript without turning observations into gates. |
| `review-source-figure-tools` | 1 | Inventory source figures, Schemes, and tables for human or model judgment. |
| `review-citation-assets` | 2 | Insert selected assets and generate stable citations and references. |
| `review-export-docx` | 3 | Export Markdown to DOCX, audit document structure, render PDF, and produce page images. |

The detailed writing philosophy is in
[`skills/review-writing-tools/SKILL.md`](skills/review-writing-tools/SKILL.md).
The compact end-to-end guide is in
[`skills/技能工作流说明.md`](skills/技能工作流说明.md).

## Repository layout

```text
ChemVellum/
├─ skills/                         # 7 agent Skills and 19 Python scripts
├─ tests/                          # maintained unittest suite
├─ view/                           # optional local artifact browser
├─ template/                       # export/reference templates
├─ review-library/                 # managed metadata and registry
├─ chem_papers/                    # local source PDFs; ignored by Git
├─ mineru-outputs/
│  ├─ markdown/                    # normalized full text; ignored by Git
│  ├─ extracted/                   # figures and structured parse output
│  └─ raw_zips/                    # original MinerU result archives
├─ review-projects/                # one generated working directory per review
└─ workspace/experiments/          # optional local demos, benchmarks, and QA
```

The ignored runtime directories are intentionally part of the layout through
tracked placeholders. Cloning the repository does not download the maintainer's
paper library or generated demonstrations.

## Requirements

- Python 3.11 or later
- packages in `requirements.txt`
- network access for external discovery and acquisition
- a MinerU API token only when parsing PDFs through MinerU
- LibreOffice or Microsoft Word for real DOCX-to-PDF conversion
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

1. Define a topic, the reader's central question, scope boundaries, and the
   comparisons that would change the answer.
2. Use `review-topic-paper-discovery` to search broadly and select candidate
   papers.
3. Put directly obtained PDFs under `chem_papers/`, or use the lawful repository
   ingestion path described by the discovery Skill.
4. Parse PDFs when necessary:

   ```bash
   python skills/mineru-precise-parse-chemvellum/scripts/parse_chemvellum_pdfs.py --input-dir chem_papers
   ```

5. Register or refresh the managed library:

   ```bash
   python skills/review-metadata-prep/scripts/prepare_metadata.py --review-root . --mineru-output mineru-outputs --pdf-root chem_papers --discover-from-pdf-root --append-registry
   ```

6. Maintain the canonical manuscript at
   `review-projects/<project_id>/manuscript.md`. Let reading and drafting reveal
   targeted follow-up searches.
7. Use the citation, asset, and export Skills when their deterministic operation
   is needed. Inspect the final PDF pages rather than treating successful export
   as editorial acceptance.

Each Skill contains its own commands and decision boundary. ChemVellum does not
require a stage machine, evidence matrix, fixed source count, figure quota, or
paragraph-by-paragraph compliance report.

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
