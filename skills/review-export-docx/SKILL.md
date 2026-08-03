---
name: review-export-docx
description: Convert review Markdown to styled academic DOCX, render that DOCX to PDF immediately, and inspect the real rendered pages.
---

# Export Review DOCX and PDF

Export whenever the manuscript is ready for layout review.

## DOCX

```bash
python skills/review-export-docx/scripts/md2docx.py \
  --input review-projects/<project_id>/deliverables/review.md \
  --output-dir review-projects/<project_id>/deliverables \
  --author "<author when known>" \
  --subject "Scholarly review manuscript" \
  --keywords "<topic keywords>"
```

When `--output` is omitted, the converter uses the manuscript's first
level-one heading as the DOCX filename. Characters that Windows does not allow
in filenames are replaced. The full title is retained whenever the destination
path permits it; in a deep project directory it is shortened at a word boundary
only enough to remain usable by Word and ordinary Windows tools. The filename
remains title-derived and never falls back to the internal project number merely
because the path is long. `--output` and `--output-dir` are mutually exclusive.

The converter supports Markdown headings, tables, images, references, inline
math, chemical subscripts/superscripts, page numbers, and academic styles.
Its default `chemvellum_journal` layout is the branded publication layout.
It keeps the title and abstract in a full-width opening section, switches the
review body and references to a compact two-column grid, and temporarily opens
full-width sections for evidence figures and comparison tables. The original
`CHEMVELLUM` wordmark uses a compressed serif treatment in deep teal and ink;
Cambria body text and captions, consistently indented prose, Arial navigation,
a padded pale-teal abstract panel, deliberate comparison-table columns, and
quiet running furniture provide a portable Word-native journal system without
copying one publisher's masthead.

Use `--layout-profile professional_single` only when a portable single-column
Word house style is explicitly needed. It uses 11 pt justified body text with a
one-em first-line indent, unindented lead paragraphs after headings and
displays, left-aligned captions, real body-list numbering, stable
hanging-indent reference labels, and table orphan protection. Use
`--layout-profile legacy_report` only when reproducing the previous 12 pt,
left-aligned, 1.5-spaced report layout is intentional.
Figure, Scheme, Chart, and Table prefixes in image alt text become caption
styles. Markdown tables render as three-line academic tables with a distinct
header style, content-aware alignment, and subtle row guidance. Scheme images
render as compact centered displays, while ordinary figures may use the full
text width. Wide tables split into readable continuation tables;
header rows repeat after page breaks and body rows stay intact when possible.
Use explicit chemistry markup when notation is ambiguous:

```markdown
CO_2_
sp^2^
10^-3^
$\ce{PdCl_2}$
```

Use `_..._` for subscripts and `^...^` for superscripts. The `~...~` form is
plain text in this converter. Keep paired scripts adjacent, as in
`g_cat._^-1^` or `NO_3_^−^`; the converter emits one compact Word object with
stacked sub- and superscripts.

## PDF and page images

Immediately render the DOCX. On Windows this uses an installed Microsoft Word
through a non-interactive COM export; it does not invoke LibreOffice:

```bash
python skills/review-export-docx/scripts/render_docx.py \
  --input "review-projects/<project_id>/deliverables/<paper title>.docx" \
  --pages-dir review-projects/<project_id>/deliverables/rendered_pages \
  --report review-projects/<project_id>/deliverables/render_report.json
```

When `--output-pdf` is omitted, the PDF uses the DOCX stem. The two final
deliverables therefore share the manuscript title.

Render the PDF from the DOCX so the inspected pages match the editable
deliverable. Open each rendered page image at readable zoom and inspect the
page itself for clipping, blank figures, broken reaction schemes, wrong
captions, font substitution, table overflow, reference wrapping, and bad page
breaks. A page counts as viewed only after its image has actually been opened;
report the exact viewed page numbers in the handoff.

`audit_docx.py` is an optional structural diagnostic:

```bash
python skills/review-export-docx/scripts/audit_docx.py \
  --input review-projects/<project_id>/deliverables/review.docx \
  --markdown review-projects/<project_id>/deliverables/review.md \
  --output-json review-projects/<project_id>/deliverables/docx_structure.json
```

Render and structural reports record conversion details; page appearance still
requires inspection of the rendered images.
