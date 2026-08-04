---
name: review-source-figure-tools
description: Browse figures, schemes, charts, spectra, images, and other visuals extracted from managed chemistry papers, then prepare source-provenance records for assets selected for a review.
---

# Source Figure Tools

Use source visuals when they communicate evidence or chemical relationships
more clearly than prose. Build the inventory promptly after the retained papers
are registered, then browse it alongside full-text reading and before sustained
drafting. Do not wait until a compact manuscript is finished and then choose a
small count of convenient images. The useful visual type depends on the subject:
reaction Schemes, spectra, chromatograms, microscopy, crystal structures,
phase diagrams, apparatus, process flows, computed surfaces, and result plots
can all carry the main evidence.

Build an inventory from the papers retained by discovery:

```bash
python skills/review-source-figure-tools/scripts/build_paper_figure_inventory.py \
  --review-root . \
  --project-id <project_id>
```

The default outputs are:

```text
review-projects/<project_id>/assets/paper_figure_inventory.json
review-projects/<project_id>/assets/paper_figure_browser.html
review-projects/<project_id>/assets/paper_figure_browser_files/
```

Open the HTML browser and inspect candidates visually. It groups them by paper
and keeps source order; it does not rank or recommend them by caption keywords.
The companion folder contains portable local previews, so the page does not
depend on permission to load arbitrary workspace paths. The JSON remains
available for deterministic insertion and provenance.

Treat every candidate as a lead. Extraction may produce page fragments, blank
crops, incomplete panels, tables, or publisher boilerplate. Before insertion,
open the complete source page and inspect the visual, caption, panel set, credit
line, and surrounding discussion.

Select visuals while reading and drafting. Treat prose, source visuals,
original high-level diagrams, and aligned tables as different views of the
project's living synthesis model. Ask which relationship the reader should see
and whether a visual exposes it more faithfully or efficiently than prose, then
choose the source asset that directly answers that question. Let the visual set
grow with the explanation rather than deciding its size in advance. Prefer a
primary source for a specific experiment, transformation, or measurement. A
review-level figure is appropriate when the text itself makes a review-level
classification or historical comparison.

For each chosen asset, create an `asset_manifest.json` row for deterministic
insertion. A reused paper asset records:

```json
{
  "asset_id": "source-visual-1",
  "kind": "figure",
  "label": "Figure 1",
  "path": "chosen/source-visual-1.png",
  "caption": "Result shown by the cited study.",
  "insert_marker": "<!-- insert:source-visual-1 -->",
  "origin": "source_paper",
  "source_paper_id": "P123",
  "source_locator": "Figure 3, page 7",
  "explanatory_use": "Distinguishes the two kinetic regimes discussed in Section 4",
  "reuse_basis": "CC BY 4.0; figure credit line checked",
  "attribution": "Reproduced from ... under CC BY 4.0 [@P123]."
}
```

Choose assets editorially after checking scientific fit, readability, and
reuse rights. `explanatory_use` states the relationship the view communicates;
it is not a required category or visual quota. The inventory and browser only
expose candidates and provenance. A selected asset should appear where it helps
the discussion unless later reading changes the model or shows that it is
unsuitable; no separate asset status file is needed.

Reaction Schemes use the same insertion path as figures. Prefer the complete,
focused source region needed by the discussion over an unrelated or unreadable
multi-panel page. Set `kind: scheme` and an explicit `label`; the DOCX converter
places it as a compact, centered display. Keep all chemistry needed to
interpret the displayed result legible.

Confirm reuse terms from the lawful source and its figure credit line. When a
suitable visual is licensed for reuse, include its source locator, reuse basis,
and attribution in the manifest. If candidates are unreadable, scientifically
unsuitable, or lack a usable permission basis, leave them out and note the
limitation briefly.

Use the stable citation form `[@P123]` in captions and attribution rather than
writing `Ref. [P123]`. Describe the licence directly. Say “with permission”
only when separate permission was actually obtained. Use “Reproduced” for an
unchanged asset and “Adapted” only when the licence permits modification and
the asset was changed.

Original high-level maps or comparison diagrams may use `origin: original`
when they express a cross-paper relationship with verified text labels and
citations. Do not rely on model-authored redraws of detailed chemical
structures or reaction Schemes. Source visuals should carry that chemistry.
