#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from html import escape
from pathlib import Path
from typing import Any


FIGURE_TYPES = {"image", "chart", "table"}
LICENSE_URL_RE = re.compile(
    r"https?://creativecommons\.org/licenses/[A-Za-z0-9_-]+(?:/[0-9.]+)?/?",
    re.I,
)
LICENSE_LINE_RE = re.compile(
    r"(?:creative commons|\bcc[- ]by\b|open access article|licensed under|copyright|©)",
    re.I,
)
OPEN_REUSE_RE = re.compile(
    r"(?:creative commons|\bcc[- ]by(?:[- ]nc|[- ]sa|[- ]nd)?\b|public domain)",
    re.I,
)
RESTRICTED_REUSE_RE = re.compile(r"all rights reserved", re.I)
SAFE_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


def validate_project_id(value: str) -> str:
    project_id = str(value or "").strip()
    if not SAFE_PROJECT_ID_RE.fullmatch(project_id) or project_id in {".", ".."}:
        raise ValueError("Unsafe project_id")
    return project_id


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean(text: Any) -> str:
    if isinstance(text, list):
        text = " ".join(str(x) for x in text if str(x).strip())
    return re.sub(r"\s+", " ", str(text or "")).strip()


def file_sha256(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_source_path(review_root: Path, value: Any) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else review_root / path


def normalize_doi(value: Any) -> str:
    doi = clean(value).lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    return doi.rstrip(".,;)")


def registry_ids_by_doi(review_root: Path) -> dict[str, str]:
    path = review_root / "review-library" / "registry" / "papers.jsonl"
    if not path.is_file():
        return {}
    index: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        paper_id = clean(row.get("paper_id"))
        doi = normalize_doi(row.get("doi"))
        if paper_id and doi:
            index.setdefault(doi, paper_id)
    return index


def selected_paper_ids(review_root: Path, project: Path) -> list[str]:
    path = project / "00_discovery" / "selected_discovery_results.json"
    data = read_json(path)
    rows = []
    if isinstance(data, dict):
        for key in ["local_papers", "selected_papers", "papers"]:
            value = data.get(key)
            if isinstance(value, list):
                rows.extend(value)
    elif isinstance(data, list):
        rows = data
    ids: list[str] = []
    seen: set[str] = set()
    if isinstance(data, dict):
        for value in data.get("candidate_paper_ids") or []:
            paper_id = clean(value)
            if paper_id and paper_id not in seen:
                seen.add(paper_id)
                ids.append(paper_id)
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("keep") is False:
            continue
        paper_id = clean(row.get("paper_id") or row.get("local_paper_id"))
        if paper_id and paper_id not in seen:
            seen.add(paper_id)
            ids.append(paper_id)

    # A download-only repository import can be promoted later by metadata prep,
    # leaving discovery rows without local paper IDs.  Recover that project-to-
    # library link by DOI so a subsequent figure inventory does not report zero
    # papers merely because candidate_update was skipped.
    if not ids and isinstance(data, dict):
        doi_index = registry_ids_by_doi(review_root)
        for row in data.get("web_papers") or []:
            if not isinstance(row, dict) or row.get("keep") is False:
                continue
            paper_id = doi_index.get(normalize_doi(row.get("doi")), "")
            if paper_id and paper_id not in seen:
                seen.add(paper_id)
                ids.append(paper_id)
    return ids


def metadata(review_root: Path, paper_id: str) -> dict[str, Any] | None:
    path = review_root / "review-library" / "metadata" / "papers" / f"{paper_id}.metadata.json"
    if not path.exists():
        return None
    data = read_json(path)
    return data if isinstance(data, dict) else None


def field_value(meta: dict[str, Any], key: str) -> Any:
    value = meta.get(key)
    if isinstance(value, dict):
        return value.get("value")
    return value


def license_hints(markdown_path: Any) -> dict[str, Any]:
    """Surface possible reuse statements without deciding that reuse is lawful."""
    raw = str(markdown_path or "").strip()
    if not raw:
        return {
            "rights_review_status": "pending",
            "reuse_hint_class": "unknown",
            "license_statement_candidates": [],
            "license_urls": [],
        }
    path = Path(raw)
    if not path.exists() or not path.is_file():
        return {
            "rights_review_status": "pending",
            "reuse_hint_class": "unknown",
            "license_statement_candidates": [],
            "license_urls": [],
        }
    text = path.read_text(encoding="utf-8", errors="ignore")
    statements: list[str] = []
    for line in text.splitlines():
        cleaned = clean(line)
        if cleaned and LICENSE_LINE_RE.search(cleaned):
            statements.append(cleaned[:600])
        if len(statements) >= 6:
            break
    urls = list(dict.fromkeys(LICENSE_URL_RE.findall(text)))
    joined = "\n".join(statements + urls)
    if OPEN_REUSE_RE.search(joined):
        hint_class = "open_reuse_candidate"
    elif RESTRICTED_REUSE_RE.search(joined):
        hint_class = "restricted"
    else:
        hint_class = "unknown"
    return {
        "rights_review_status": "license_hint_found" if statements or urls else "pending",
        "reuse_hint_class": hint_class,
        "license_statement_candidates": list(dict.fromkeys(statements)),
        "license_urls": urls[:6],
        "instructions": (
            "These are discovery hints only. Verify the article license, the selected figure's "
            "credit line, any third-party exclusion, and whether adaptation is permitted."
        ),
    }


def crop_spec(
    source_pdf: Any,
    page_idx: Any,
    bbox: Any,
) -> dict[str, Any] | None:
    raw_pdf = str(source_pdf or "").strip()
    if not raw_pdf or not isinstance(page_idx, int):
        return None
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        coords = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return None
    if coords[2] <= coords[0] or coords[3] <= coords[1]:
        return None
    return {"source_pdf": raw_pdf, "page_index": page_idx, "bbox": coords}


def materialize_candidate_image(project: Path, candidate: dict[str, Any]) -> str:
    """Resolve a missing MinerU image by cropping its recorded PDF page and bbox."""
    existing = str(candidate.get("source_image_path") or "").strip()
    if existing and Path(existing).exists():
        return existing
    spec = candidate.get("source_crop")
    if not isinstance(spec, dict):
        return ""
    pdf_path = Path(str(spec.get("source_pdf") or ""))
    if not pdf_path.is_absolute():
        pdf_path = project.parents[1] / pdf_path
    if not pdf_path.exists():
        return ""
    page_index = spec.get("page_index")
    bbox = spec.get("bbox")
    if not isinstance(page_index, int) or not isinstance(bbox, list) or len(bbox) != 4:
        return ""
    try:
        import fitz

        with fitz.open(pdf_path) as document:
            if page_index < 0 or page_index >= document.page_count:
                return ""
            page = document.load_page(page_index)
            rect = fitz.Rect(*[float(value) for value in bbox]) & page.rect
            if rect.is_empty or rect.width < 5 or rect.height < 5:
                return ""
            digest = hashlib.sha256(
                f"{pdf_path.resolve()}|{page_index}|{','.join(str(value) for value in bbox)}".encode("utf-8")
            ).hexdigest()[:16]
            paper_id = re.sub(r"[^A-Za-z0-9_-]+", "-", str(candidate.get("paper_id") or "paper"))
            out_dir = project / "02_section_drafting" / "source_figure_crops"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{paper_id}-p{page_index + 1}-{digest}.png"
            if not out_path.exists():
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), clip=rect, alpha=False)
                pixmap.save(out_path)
    except Exception:
        return ""
    return str(out_path.resolve())


def block_caption(block: dict[str, Any]) -> str:
    parts = []
    for key in ["image_caption", "table_caption", "caption", "text"]:
        value = block.get(key)
        text = clean(value)
        if text:
            parts.append(text)
    return clean(" ".join(parts))


def infer_source_label(caption: str, index: int, source_type: str) -> str:
    match = re.search(r"\b(Scheme|Figure|Fig\.|Table)\s*[\w.-]+", caption, re.I)
    if match:
        return match.group(0).replace("Fig.", "Figure")
    prefix = "Table" if source_type == "table" else "Figure"
    return f"{prefix} candidate {index}"


def split_figure_groups(blocks: list[Any]) -> dict[int, list[int]]:
    figure_indexes = [
        index
        for index, block in enumerate(blocks)
        if isinstance(block, dict) and block.get("type") in FIGURE_TYPES
    ]
    groups: dict[int, list[int]] = {}
    for left, right in zip(figure_indexes, figure_indexes[1:]):
        left_block = blocks[left]
        right_block = blocks[right]
        if left_block.get("page_idx") != right_block.get("page_idx"):
            continue
        continuation = re.match(r"^\(?([A-Z])\)?[.)]\s*", block_caption(right_block))
        if continuation and re.search(
            rf"\({re.escape(continuation.group(1))}\)",
            block_caption(left_block),
            re.I,
        ):
            group = [left, right]
            groups[left] = group
            groups[right] = group
    return groups


def local_file_uri(raw: Any) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    path = Path(value)
    return path.resolve().as_uri() if path.is_file() else ""


def browser_preview_path(
    candidate: dict[str, Any],
    output: Path,
) -> tuple[str, str]:
    """Copy one available preview beside the browser and return its relative path."""
    source_path = ""
    preview_note = ""
    raw_source = str(candidate.get("source_image_path") or "").strip()
    if raw_source and Path(raw_source).is_file():
        source_path = raw_source
        preview_note = "extracted image"
    fragments = candidate.get("source_fragment_paths")
    if not source_path and isinstance(fragments, list):
        for fragment in fragments:
            raw_fragment = str(fragment or "").strip()
            if raw_fragment and Path(raw_fragment).is_file():
                source_path = raw_fragment
                preview_note = "partial extraction; inspect the complete source page"
                break
    if source_path:
        source = Path(source_path)
        asset_dir = output.parent / f"{output.stem}_files"
        asset_dir.mkdir(parents=True, exist_ok=True)
        candidate_id = re.sub(
            r"[^A-Za-z0-9_-]+",
            "-",
            str(candidate.get("inventory_candidate_id") or "visual"),
        ).strip("-")
        digest = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:10]
        suffix = source.suffix.lower() if source.suffix else ".img"
        target = asset_dir / f"{candidate_id}-{digest}{suffix}"
        if not target.exists() or target.stat().st_size != source.stat().st_size:
            shutil.copy2(source, target)
        return target.relative_to(output.parent).as_posix(), preview_note
    remote = str(candidate.get("repository_image_url") or "").strip()
    if remote.startswith(("https://", "http://")):
        return remote, "repository preview"
    return "", "open the source document"


def render_browser_html(inventory: dict[str, Any], output: Path) -> None:
    """Render a source-ordered visual browser without recommending candidates."""
    project_id = escape(str(inventory.get("project_id") or "review"))
    sections: list[str] = []
    for paper in inventory.get("papers") or []:
        if not isinstance(paper, dict):
            continue
        paper_id = escape(str(paper.get("paper_id") or ""))
        title = escape(str(paper.get("title") or "Untitled paper"))
        cards: list[str] = []
        for candidate in paper.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            preview_uri, preview_note = browser_preview_path(candidate, output)
            preview = (
                f'<img loading="lazy" src="{escape(preview_uri, quote=True)}" '
                f'alt="{escape(str(candidate.get("source_label") or "source visual"), quote=True)}">'
                if preview_uri
                else '<div class="no-preview">No extracted preview</div>'
            )
            source_document = local_file_uri(candidate.get("source_document"))
            page_index = candidate.get("source_page_index")
            if source_document and isinstance(page_index, int):
                source_document = f"{source_document}#page={page_index + 1}"
            source_link = (
                f'<a href="{escape(source_document, quote=True)}">Open source</a>'
                if source_document
                else "<span>Source file unavailable</span>"
            )
            rights = candidate.get("reuse_rights_hints") or {}
            rights_class = escape(str(rights.get("reuse_hint_class") or "unknown"))
            label = escape(str(candidate.get("source_label") or "Unlabelled visual"))
            source_type = escape(str(candidate.get("source_type") or "visual"))
            locator = escape(
                str(
                    candidate.get("source_locator")
                    or candidate.get("source_page_hint")
                    or ""
                )
            )
            caption = escape(str(candidate.get("source_caption_text") or "No caption extracted."))
            search_text = escape(
                " ".join(
                    [
                        str(paper.get("paper_id") or ""),
                        str(paper.get("title") or ""),
                        str(candidate.get("source_label") or ""),
                        str(candidate.get("source_type") or ""),
                        str(candidate.get("source_caption_text") or ""),
                    ]
                ).lower(),
                quote=True,
            )
            cards.append(
                f"""
                <article class="candidate" data-search="{search_text}">
                  <div class="preview">{preview}</div>
                  <div class="candidate-body">
                    <div class="badges">
                      <span>{source_type}</span><span>{locator or "locator pending"}</span>
                      <span>rights: {rights_class}</span>
                    </div>
                    <h3>{label}</h3>
                    <p>{caption}</p>
                    <p class="preview-note">{escape(preview_note)}</p>
                    <div class="links">{source_link}</div>
                  </div>
                </article>
                """
            )
        sections.append(
            f"""
            <section class="paper" data-paper="{paper_id}">
              <h2>{paper_id} · {title}</h2>
              <p class="paper-count">{len(cards)} extracted visual candidates, shown in source order.</p>
              <div class="grid">{''.join(cards) if cards else '<p>No extracted candidates.</p>'}</div>
            </section>
            """
        )

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Source visual browser · {project_id}</title>
  <style>
    :root {{ color-scheme: light; font-family: Arial, sans-serif; }}
    body {{ margin: 0; background: #f4f5f7; color: #17202a; }}
    header {{ position: sticky; top: 0; z-index: 2; padding: 18px 24px;
      background: rgba(255,255,255,.96); border-bottom: 1px solid #d8dde3; }}
    header h1 {{ margin: 0 0 8px; font-size: 22px; }}
    header p {{ margin: 4px 0 12px; max-width: 960px; line-height: 1.45; }}
    input {{ width: min(760px, 92vw); padding: 10px 12px; border: 1px solid #aeb7c2;
      border-radius: 6px; font-size: 15px; }}
    main {{ padding: 8px 24px 40px; }}
    .paper {{ margin: 22px auto 34px; max-width: 1500px; }}
    .paper h2 {{ margin-bottom: 4px; font-size: 19px; }}
    .paper-count, .preview-note {{ color: #59636e; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
      gap: 16px; }}
    .candidate {{ display: grid; grid-template-rows: 230px auto; min-width: 0;
      background: white; border: 1px solid #d8dde3; border-radius: 8px; overflow: hidden; }}
    .preview {{ display: flex; align-items: center; justify-content: center;
      background: white; border-bottom: 1px solid #e1e5ea; }}
    .preview img {{ width: 100%; height: 100%; object-fit: contain; }}
    .no-preview {{ color: #7a838d; }}
    .candidate-body {{ padding: 13px 15px 15px; }}
    .candidate h3 {{ margin: 10px 0 7px; font-size: 17px; }}
    .candidate p {{ margin: 7px 0; line-height: 1.4; }}
    .badges {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .badges span {{ padding: 3px 7px; border-radius: 999px; background: #edf1f5;
      font-size: 12px; }}
    .links a {{ color: #075cba; }}
    [hidden] {{ display: none !important; }}
  </style>
</head>
<body>
  <header>
    <h1>Source visual browser · {project_id}</h1>
    <p>Candidates are grouped by paper and kept in source order. Browse the images and
    captions directly; no keyword score or automatic recommendation has selected them.
    Before reuse, open the complete source page and verify scientific fit, readability,
    credit, and licence.</p>
    <input id="filter" type="search" placeholder="Filter by paper, caption, label, or visual type">
  </header>
  <main>{''.join(sections)}</main>
  <script>
    const filter = document.getElementById('filter');
    filter.addEventListener('input', () => {{
      const query = filter.value.trim().toLowerCase();
      document.querySelectorAll('.candidate').forEach(card => {{
        card.hidden = query && !card.dataset.search.includes(query);
      }});
      document.querySelectorAll('.paper').forEach(section => {{
        const visible = [...section.querySelectorAll('.candidate')].some(card => !card.hidden);
        section.hidden = query && !visible;
      }});
    }});
  </script>
</body>
</html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")


def build_inventory(review_root: Path, project_id: str) -> dict[str, Any]:
    project = review_root / "review-projects" / project_id
    ids = selected_paper_ids(review_root, project)
    papers = []
    all_candidates: list[dict[str, Any]] = []
    for paper_id in ids:
        meta = metadata(review_root, paper_id)
        if not meta:
            papers.append(
                {
                    "paper_id": paper_id,
                    "status": "missing_metadata",
                    "candidate_count": 0,
                    "candidates": [],
                }
            )
            continue
        source_paths = meta.get("source_paths") or {}
        raw_pdf_path = str(source_paths.get("pdf") or "").strip()
        source_pdf_path = Path(raw_pdf_path) if raw_pdf_path else None
        if source_pdf_path is not None and not source_pdf_path.is_absolute():
            source_pdf_path = review_root / source_pdf_path
        source_pdf_sha256 = file_sha256(source_pdf_path)
        raw_xml_path = str(source_paths.get("xml") or "").strip()
        source_xml_path = Path(raw_xml_path) if raw_xml_path else None
        if source_xml_path is not None and not source_xml_path.is_absolute():
            source_xml_path = review_root / source_xml_path
        source_document_path = (
            source_pdf_path
            if source_pdf_path is not None and source_pdf_path.is_file()
            else source_xml_path
        )
        source_document_kind = (
            "pdf"
            if source_pdf_path is not None and source_pdf_path.is_file()
            else "jats_xml"
            if source_xml_path is not None and source_xml_path.is_file()
            else ""
        )
        source_document_sha256 = file_sha256(source_document_path)
        markdown_path = resolve_source_path(review_root, source_paths.get("markdown"))
        rights_hints = license_hints(markdown_path)
        raw_content_path = str(source_paths.get("content_list") or "").strip()
        raw_extracted_dir = str(source_paths.get("extracted_dir") or "").strip()
        content_path = resolve_source_path(review_root, raw_content_path)
        extracted_dir = resolve_source_path(review_root, raw_extracted_dir)
        candidates = []
        if content_path and content_path.is_file():
            blocks = read_json(content_path)
            if isinstance(blocks, list):
                split_groups = split_figure_groups(blocks)
                for block_index, block in enumerate(blocks):
                    if not isinstance(block, dict) or block.get("type") not in FIGURE_TYPES:
                        continue
                    img_rel = block.get("img_path") or block.get("image_path") or block.get("path")
                    crop_pdf = (
                        str(source_pdf_path)
                        if source_pdf_path is not None
                        else source_paths.get("pdf")
                    )
                    source_image_path = str((extracted_dir / str(img_rel)).resolve()) if img_rel and extracted_dir and extracted_dir.is_dir() else ""
                    source_crop = crop_spec(
                        crop_pdf,
                        block.get("page_idx"),
                        block.get("bbox"),
                    )
                    fragment_indexes = split_groups.get(block_index, [])
                    fragment_paths = [
                        str((extracted_dir / str(fragment_rel)).resolve())
                        for fragment_index in fragment_indexes
                        for fragment_rel in [
                            blocks[fragment_index].get("img_path")
                            or blocks[fragment_index].get("image_path")
                            or blocks[fragment_index].get("path")
                        ]
                        if fragment_rel and extracted_dir and (extracted_dir / str(fragment_rel)).exists()
                    ]
                    caption = block_caption(block)
                    source_type = str(block.get("type") or "")
                    candidates.append(
                        {
                            "inventory_candidate_id": f"{paper_id}-V{block_index + 1:04d}",
                            "paper_id": paper_id,
                            "title": field_value(meta, "title"),
                            "source_label": infer_source_label(caption, len(candidates) + 1, source_type),
                            "source_type": source_type,
                            "source_pdf": source_paths.get("pdf"),
                            "source_pdf_sha256": source_pdf_sha256,
                            "source_document": (
                                str(source_document_path)
                                if source_document_path is not None
                                else ""
                            ),
                            "source_document_kind": source_document_kind,
                            "source_document_sha256": source_document_sha256,
                            "source_page_index": block.get("page_idx"),
                            "source_bbox": block.get("bbox"),
                            "source_locator": block.get("source_locator"),
                            "repository_provider": block.get("repository_provider"),
                            "repository_id": block.get("repository_id"),
                            "repository_figure_id": block.get("repository_figure_id"),
                            "repository_graphic_href": block.get("repository_graphic_href"),
                            "repository_image_url": block.get("repository_image_url"),
                            "source_content_list": str(content_path),
                            "source_image_path": (
                                source_image_path
                                if not fragment_indexes and source_image_path and Path(source_image_path).exists()
                                else ""
                            ),
                            "source_image_sha256": file_sha256(
                                Path(source_image_path) if source_image_path else None
                            ),
                            # Split panels must be reconstructed from the whole
                            # source page; cropping one MinerU block would create
                            # a deceptively incomplete figure.
                            "source_crop": source_crop if not fragment_indexes else None,
                            "source_resolution_status": (
                                "extracted_image"
                                if not fragment_indexes and source_image_path and Path(source_image_path).exists()
                                else "pdf_crop_available"
                                if not fragment_indexes and source_crop
                                else "needs_source_review"
                            ),
                            "source_completeness": "mineru_split" if fragment_indexes else "single_block",
                            "source_fragment_paths": fragment_paths,
                            "source_page_hint": (
                                f"page {int(block.get('page_idx', 0)) + 1}"
                                if block.get("page_idx") is not None
                                else (
                                    f"repository figure {block.get('repository_figure_id')}"
                                    if block.get("repository_figure_id")
                                    else ""
                                )
                            ),
                            "source_caption_text": caption,
                            "reuse_rights_hints": rights_hints,
                            "human_reading_hint": "Prefer when the asset answers a named reader question or compresses a comparison, mechanism, evidence boundary, or process relationship.",
                        }
                    )
        all_candidates.extend(candidates)
        papers.append(
            {
                "paper_id": paper_id,
                "title": field_value(meta, "title"),
                "source_pdf": source_paths.get("pdf"),
                "source_document": (
                    str(source_document_path)
                    if source_document_path is not None
                    else ""
                ),
                "source_document_kind": source_document_kind,
                "markdown": source_paths.get("markdown"),
                "content_list": source_paths.get("content_list"),
                "reuse_rights_hints": rights_hints,
                "candidate_count": len(candidates),
                "candidates": candidates,
            }
        )
    return {
        "project_id": project_id,
        "paper_count": len(ids),
        "candidate_count": len(all_candidates),
        "figure_candidate_count": sum(
            candidate.get("source_type") in {"image", "chart"}
            for candidate in all_candidates
        ),
        "table_candidate_count": sum(
            candidate.get("source_type") == "table" for candidate in all_candidates
        ),
        "license_hint_candidate_count": sum(
            (candidate.get("reuse_rights_hints") or {}).get("rights_review_status")
            == "license_hint_found"
            for candidate in all_candidates
        ),
        "open_reuse_hint_candidate_count": sum(
            (candidate.get("reuse_rights_hints") or {}).get("reuse_hint_class")
            == "open_reuse_candidate"
            for candidate in all_candidates
        ),
        # Keep both a flat self-describing view and the per-paper grouping.
        # Consumers can no longer mistake nested candidates for an empty set.
        "candidates": all_candidates,
        "papers": papers,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory extracted paper figures with source provenance.")
    parser.add_argument("--review-root", default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument("--project-id", required=True)
    parser.add_argument(
        "--output",
        help="Optional output JSON path. Defaults to <project>/assets/paper_figure_inventory.json.",
    )
    parser.add_argument(
        "--browser-output",
        help="Optional HTML browser path. Defaults to <project>/assets/paper_figure_browser.html.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Write only the JSON inventory.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    review_root = Path(args.review_root).resolve()
    project_id = validate_project_id(args.project_id)
    project = review_root / "review-projects" / project_id
    if not project.exists():
        raise SystemExit(f"Project not found: {project}")
    out = (
        Path(args.output).resolve()
        if args.output
        else project / "assets" / "paper_figure_inventory.json"
    )
    inventory = build_inventory(review_root, project_id)
    write_json(out, inventory)
    browser = (
        Path(args.browser_output).resolve()
        if args.browser_output
        else project / "assets" / "paper_figure_browser.html"
    )
    if not args.no_browser:
        render_browser_html(inventory, browser)
    print(f"Wrote {out}")
    if not args.no_browser:
        print(f"Wrote {browser}")
    print(f"Papers: {inventory['paper_count']}")
    print(
        "Candidates: "
        f"{inventory['candidate_count']} total; "
        f"{inventory['figure_candidate_count']} figures/charts; "
        f"{inventory['table_candidate_count']} tables; "
        f"{inventory['license_hint_candidate_count']} with licence hints; "
        f"{inventory['open_reuse_hint_candidate_count']} possible open-reuse candidates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
