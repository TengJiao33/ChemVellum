#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def write_ingest_receipt(path: Path, payload: dict[str, Any]) -> None:
    write_json(path, payload)


def field_value(value: Any) -> Any:
    return value.get("value") if isinstance(value, dict) and "value" in value else value


def normalized_doi(value: Any) -> str:
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", str(value or "").strip(), flags=re.I).lower()


def normalized_title(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def load_managed_metadata(review_root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted((review_root / "review-library" / "metadata" / "papers").glob("*.metadata.json")):
        try:
            payload = read_json(path)
        except Exception:
            continue
        if isinstance(payload, dict) and payload.get("paper_id"):
            result[str(payload["paper_id"])] = payload
    return result


def match_managed_paper(
    item: dict[str, Any],
    metadata: dict[str, dict[str, Any]],
    preferred_ids: set[str],
) -> tuple[str | None, dict[str, Any] | None]:
    target_name = Path(str(item.get("target_pdf_path") or "")).name.lower()
    wanted_doi = normalized_doi(item.get("doi"))
    wanted_title = normalized_title(item.get("title"))
    ranked_ids = sorted(metadata, key=lambda paper_id: paper_id not in preferred_ids)
    for paper_id in ranked_ids:
        row = metadata[paper_id]
        source_paths = row.get("source_paths") or {}
        source_file = row.get("source_file") or {}
        pdf_names = {
            Path(str(source_paths.get("pdf") or "")).name.lower(),
            Path(str(source_file.get("relative_pdf_path") or "")).name.lower(),
            Path(str(source_file.get("pdf_name") or "")).name.lower(),
        }
        if target_name and target_name in pdf_names:
            return paper_id, row
        if wanted_doi and normalized_doi(field_value(row.get("doi"))) == wanted_doi:
            return paper_id, row
        if wanted_title and normalized_title(field_value(row.get("title"))) == wanted_title:
            return paper_id, row
    return None, None


def external_field(value: Any, confidence: float = 0.99) -> dict[str, Any]:
    return {
        "value": value,
        "source": "external_discovery",
        "confidence": confidence,
        "human_checked": False,
    }


def hydrate_plan_bibliography(discovery_dir: Path, plan: dict[str, Any]) -> None:
    selected_path = discovery_dir / "selected_discovery_results.json"
    if not selected_path.exists():
        return
    selected = read_json(selected_path)
    web_rows = selected.get("web_papers") or [] if isinstance(selected, dict) else []
    by_key: dict[str, dict[str, Any]] = {}
    for row in web_rows:
        if not isinstance(row, dict):
            continue
        for key in (str(row.get("external_id") or ""), normalized_doi(row.get("doi"))):
            if key:
                by_key[key] = row
    for item in plan.get("items") or []:
        if not isinstance(item, dict):
            continue
        row = by_key.get(str(item.get("paper_key") or "")) or by_key.get(
            normalized_doi(item.get("doi"))
        )
        if not row:
            continue
        for key in ("authors", "journal", "abstract"):
            if not item.get(key) and row.get(key):
                item[key] = row[key]


def update_registry_bibliography(
    review_root: Path,
    paper_id: str,
    metadata: dict[str, Any],
) -> None:
    registry_path = review_root / "review-library" / "registry" / "papers.jsonl"
    if not registry_path.exists():
        return
    rows: list[dict[str, Any]] = []
    changed = False
    for line in registry_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict) and str(row.get("paper_id")) == paper_id:
            row.update(
                {
                    "title": field_value(metadata.get("title")),
                    "authors": field_value(metadata.get("authors")),
                    "year": field_value(metadata.get("year")),
                    "journal": field_value(metadata.get("journal")),
                    "doi": field_value(metadata.get("doi")),
                }
            )
            changed = True
        rows.append(row)
    if not changed:
        return
    temp = registry_path.with_suffix(".jsonl.tmp")
    temp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    temp.replace(registry_path)


def reconcile_bibliographic_metadata(
    review_root: Path,
    paper_id: str,
    metadata: dict[str, Any],
    item: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    reconciled = dict(metadata)
    changed_fields: list[str] = []
    wanted_title = str(item.get("title") or "").strip()
    wanted_authors = [str(value).strip() for value in item.get("authors") or [] if str(value).strip()]
    wanted_doi = normalized_doi(item.get("doi"))
    wanted_year = item.get("year")
    wanted_journal = str(item.get("journal") or "").strip()
    wanted_abstract = str(item.get("abstract") or "").strip()
    if wanted_title and field_value(reconciled.get("title")) != wanted_title:
        reconciled["title"] = external_field(wanted_title)
        changed_fields.append("title")
    if wanted_doi and normalized_doi(field_value(reconciled.get("doi"))) != wanted_doi:
        reconciled["doi"] = external_field(wanted_doi)
        changed_fields.append("doi")
    if wanted_year and str(field_value(reconciled.get("year")) or "") != str(wanted_year):
        reconciled["year"] = external_field(wanted_year, 0.98)
        changed_fields.append("year")
    if wanted_authors and field_value(reconciled.get("authors")) != wanted_authors:
        reconciled["authors"] = external_field(wanted_authors)
        changed_fields.append("authors")
    if wanted_journal and field_value(reconciled.get("journal")) != wanted_journal:
        reconciled["journal"] = external_field(wanted_journal)
        changed_fields.append("journal")
    if wanted_abstract and field_value(reconciled.get("abstract")) != wanted_abstract:
        reconciled["abstract"] = external_field(wanted_abstract, 0.98)
        changed_fields.append("abstract")
    if not changed_fields:
        return reconciled, []
    extraction = reconciled.setdefault("extraction", {})
    notes = extraction.setdefault("notes", []) if isinstance(extraction, dict) else []
    note = "bibliographic_reconciled_from_external_discovery:" + ",".join(changed_fields)
    if isinstance(notes, list) and note not in notes:
        notes.append(note)
    quality = reconciled.get("quality")
    if isinstance(quality, dict):
        resolved_warning_names = {
            "title": {"low_confidence_title", "missing_title"},
            "authors": {"missing_authors"},
            "year": {"missing_year"},
            "journal": {"missing_journal"},
            "doi": {"missing_doi"},
            "abstract": {"missing_abstract", "low_confidence_abstract"},
        }
        resolved_warnings = set().union(
            *(resolved_warning_names.get(field, set()) for field in changed_fields)
        )
        warnings = [
            str(value)
            for value in quality.get("warnings") or []
            if str(value) not in resolved_warnings
        ]
        quality["missing_fields"] = [
            str(value)
            for value in quality.get("missing_fields") or []
            if str(value) not in set(changed_fields)
        ]
        quality["warnings"] = warnings
        confidences = [
            float(reconciled.get(key, {}).get("confidence") or 0)
            for key in ("title", "authors", "year", "journal", "doi", "abstract", "structured_tags")
            if isinstance(reconciled.get(key), dict)
        ]
        quality["overall_confidence"] = round(sum(confidences) / len(confidences), 3) if confidences else 0
        quality["needs_human_check"] = bool(quality.get("missing_fields") or warnings)
    metadata_path = review_root / "review-library" / "metadata" / "papers" / f"{paper_id}.metadata.json"
    write_json(metadata_path, reconciled)
    update_registry_bibliography(review_root, paper_id, reconciled)
    return reconciled, changed_fields


def reconcile_existing_plan_metadata(
    review_root: Path,
    plan: dict[str, Any],
    metadata: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    reconciled: list[dict[str, Any]] = []
    for item in plan.get("items") or []:
        if not isinstance(item, dict) or item.get("action") != "use_local":
            continue
        paper_id = str(item.get("local_paper_id") or "")
        metadata_row = metadata.get(paper_id)
        if not paper_id or not metadata_row:
            continue
        metadata_row, changed_fields = reconcile_bibliographic_metadata(
            review_root,
            paper_id,
            metadata_row,
            item,
        )
        metadata[paper_id] = metadata_row
        if changed_fields:
            reconciled.append({"paper_id": paper_id, "fields": changed_fields})
    return reconciled


def discovery_candidate(
    paper_id: str,
    metadata: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "title": field_value(metadata.get("title")) or item.get("title"),
        "year": field_value(metadata.get("year")) or item.get("year"),
        "journal": field_value(metadata.get("journal")),
        "abstract": field_value(metadata.get("abstract")) or "",
        "structured_tags": field_value(metadata.get("structured_tags")) or {},
        "source_paths": metadata.get("source_paths") or {},
        "role": "uncertain",
        "matched_keywords": [str(value) for value in item.get("matched_keywords") or [] if value],
        "best_score": 0,
        "ranking_score": 0,
        "keep": True,
        "discovery_source": "external_promoted",
        "external_paper_key": item.get("paper_key"),
    }


def add_promotions_to_candidates(
    discovery_dir: Path,
    promotions: list[tuple[dict[str, Any], str, dict[str, Any]]],
) -> list[str]:
    selected_path = discovery_dir / "selected_discovery_results.json"
    if not selected_path.exists() or not promotions:
        return []
    selected = read_json(selected_path)
    if not isinstance(selected, dict):
        raise ValueError("selected_discovery_results.json must contain an object")
    local_papers = selected.setdefault("local_papers", [])
    if not isinstance(local_papers, list):
        raise ValueError("selected discovery candidates must be a list")
    existing_ids = {
        str(row.get("paper_id"))
        for row in local_papers
        if isinstance(row, dict) and row.get("paper_id")
    }
    promoted_ids: list[str] = []
    for item, paper_id, metadata in promotions:
        if paper_id not in existing_ids:
            local_papers.append(discovery_candidate(paper_id, metadata, item))
            existing_ids.add(paper_id)
        promoted_ids.append(paper_id)
    candidate_ids = [str(row.get("paper_id")) for row in local_papers if isinstance(row, dict) and row.get("paper_id")]
    selected["candidate_paper_ids"] = list(dict.fromkeys(candidate_ids))
    selected["newly_ingested_paper_ids"] = list(dict.fromkeys(promoted_ids))
    write_json(selected_path, selected)
    return list(dict.fromkeys(promoted_ids))


def safe_target(review_root: Path, relative_path: str) -> Path:
    import_root = (review_root / "chem_papers" / "web-imports").resolve()
    target = (review_root / relative_path).resolve()
    try:
        target.relative_to(import_root)
    except ValueError as exc:
        raise ValueError(f"target_pdf_path escapes chem_papers/web-imports: {relative_path}") from exc
    if target.suffix.lower() != ".pdf":
        raise ValueError(f"target_pdf_path must end in .pdf: {relative_path}")
    return target


class PdfLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata_urls: list[str] = []
        self.link_urls: list[str] = []
        self.anchor_urls: list[str] = []
        self._anchor_href = ""
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag.lower() == "meta":
            name = (values.get("name") or values.get("property") or "").lower()
            if name in {"citation_pdf_url", "dc.identifier.pdf", "og:pdf"} and values.get("content"):
                self.metadata_urls.append(values["content"])
        elif tag.lower() == "link":
            content_type = values.get("type", "").lower()
            href = values.get("href", "")
            if href and (content_type == "application/pdf" or _looks_like_pdf_url(href)):
                self.link_urls.append(href)
        elif tag.lower() == "a":
            self._anchor_href = values.get("href", "")
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._anchor_href:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._anchor_href:
            return
        label = " ".join(self._anchor_text).strip().lower()
        if _looks_like_pdf_url(self._anchor_href) or "pdf" in label:
            self.anchor_urls.append(self._anchor_href)
        self._anchor_href = ""
        self._anchor_text = []


class ArticleImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.image_urls: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        src = values.get("src", "").strip()
        if not src:
            return
        filename = Path(urllib.parse.urlparse(src).path).name
        if filename:
            self.image_urls.setdefault(filename, src)


def _looks_like_pdf_url(url: str) -> bool:
    path = urllib.parse.urlparse(str(url or "")).path.lower().rstrip("/")
    return path.endswith(".pdf") or path.endswith("/pdf")


def extract_pdf_urls(page_url: str, html: str) -> list[str]:
    parser = PdfLinkParser()
    parser.feed(html)
    urls: list[str] = []
    for candidate in parser.metadata_urls + parser.link_urls + parser.anchor_urls:
        absolute = urllib.parse.urljoin(page_url, candidate)
        parsed = urllib.parse.urlparse(absolute)
        if parsed.scheme in {"http", "https"} and parsed.netloc and absolute not in urls:
            urls.append(absolute)
    return urls


def europe_pmc_article_images(article_url: str, timeout: int) -> dict[str, str]:
    request = urllib.request.Request(
        article_url,
        headers={"User-Agent": "chemvellum-ingest/0.3"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        resolved_url = response.geturl()
        html = response.read(10 * 1024 * 1024).decode("utf-8", errors="replace")
    parser = ArticleImageParser()
    parser.feed(html)
    images: dict[str, str] = {}
    for filename, raw_url in parser.image_urls.items():
        absolute = urllib.parse.urljoin(resolved_url, raw_url)
        host = (urllib.parse.urlparse(absolute).hostname or "").lower()
        if host.endswith(".nih.gov") or host.endswith(".ncbi.nlm.nih.gov"):
            images[filename] = absolute
    return images


def download_repository_image(url: str, target: Path, timeout: int) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "chemvellum-ingest/0.3"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(32 * 1024 * 1024)
    signatures = (
        b"\xff\xd8\xff",
        b"\x89PNG\r\n\x1a\n",
        b"GIF87a",
        b"GIF89a",
        b"II*\x00",
        b"MM\x00*",
        b"RIFF",
    )
    if not payload.startswith(signatures):
        raise ValueError("repository figure response is not a recognized image")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def download_europe_pmc_assets(
    pmcid: str,
    image_dir: Path,
    timeout: int,
) -> tuple[dict[str, Path], str]:
    archive_url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/"
        f"{pmcid}/supplementaryFiles"
    )
    request = urllib.request.Request(
        archive_url,
        headers={"User-Agent": "chemvellum-ingest/0.3"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        archive_bytes = response.read(128 * 1024 * 1024)
    assets: dict[str, Path] = {}
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for member in archive.infolist():
            filename = Path(member.filename).name
            suffix = Path(filename).suffix.lower()
            if not filename or suffix not in {
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".tif",
                ".tiff",
                ".webp",
            }:
                continue
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", filename)
            target = image_dir / safe_name
            payload = archive.read(member)
            signatures = (
                b"\xff\xd8\xff",
                b"\x89PNG\r\n\x1a\n",
                b"GIF87a",
                b"GIF89a",
                b"II*\x00",
                b"MM\x00*",
                b"RIFF",
            )
            if not payload.startswith(signatures):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            assets[filename] = target
    return assets, archive_url


def resolve_pdf_url(page_url: str, timeout: int) -> str:
    parsed = urllib.parse.urlparse(page_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"unsupported full-text URL: {page_url}")
    request = urllib.request.Request(
        page_url,
        headers={"User-Agent": "chemvellum-ingest/0.3"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        resolved_url = response.geturl()
        content_type = str(response.headers.get("Content-Type") or "").lower()
        first = response.read(8192)
        if first.startswith(b"%PDF-") or "application/pdf" in content_type:
            return resolved_url
        payload = first + response.read(5 * 1024 * 1024)
    html = payload.decode("utf-8", errors="replace")
    candidates = extract_pdf_urls(resolved_url, html)
    if not candidates:
        raise ValueError("full-text page did not expose a PDF link")
    return candidates[0]


def europe_pmc_article_url(doi: Any, timeout: int) -> str:
    normalized = normalized_doi(doi)
    if not normalized:
        raise ValueError("Europe PMC fallback requires a DOI")
    query = urllib.parse.urlencode(
        {
            "query": f'DOI:"{normalized}"',
            "format": "json",
            "pageSize": 3,
        }
    )
    api_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{query}"
    request = urllib.request.Request(
        api_url,
        headers={"User-Agent": "chemvellum-ingest/0.2"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    results = (payload.get("resultList") or {}).get("result") or []
    for result in results:
        if normalized_doi(result.get("doi")) != normalized:
            continue
        pmcid = str(result.get("pmcid") or "").strip().upper()
        if pmcid and result.get("isOpenAccess") == "Y" and result.get("inEPMC") == "Y":
            return f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
    raise ValueError("Europe PMC has no matching open full-text record")


def _xml_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return re.sub(r"\s+", " ", " ".join(element.itertext())).strip()


def _first_xml_element(root: ET.Element, local_name: str) -> ET.Element | None:
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == local_name:
            return element
    return None


def download_europe_pmc_jats(
    article_url: str,
    target: Path,
    review_root: Path,
    timeout: int,
) -> tuple[Path, Path]:
    match = re.search(r"/articles/(PMC\d+)", article_url, re.I)
    if not match:
        raise ValueError("Europe PMC article URL does not contain a PMCID")
    pmcid = match.group(1).upper()
    api_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    request = urllib.request.Request(
        api_url,
        headers={"User-Agent": "chemvellum-ingest/0.2"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        xml_bytes = response.read(32 * 1024 * 1024)
    root = ET.fromstring(xml_bytes)
    if root.tag.rsplit("}", 1)[-1] != "article":
        raise ValueError("Europe PMC full-text response is not a JATS article")

    slug = mineru_slug_for_target(target, review_root / "chem_papers")
    xml_path = target.with_suffix(".jats.xml")
    markdown_path = review_root / "mineru-outputs" / "markdown" / f"{slug}.md"
    extracted_dir = review_root / "mineru-outputs" / "extracted" / slug
    content_path = extracted_dir / f"{slug}_content_list.json"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    extracted_dir.mkdir(parents=True, exist_ok=True)
    image_dir = extracted_dir / "images"
    try:
        repository_assets, repository_archive_url = download_europe_pmc_assets(
            pmcid,
            image_dir,
            timeout,
        )
    except Exception:
        repository_assets, repository_archive_url = {}, ""
    try:
        article_images = europe_pmc_article_images(article_url, timeout)
    except Exception:
        article_images = {}

    title = _xml_text(_first_xml_element(root, "article-title")) or slug.replace("-", " ")
    doi = ""
    for article_id in root.iter():
        if article_id.tag.rsplit("}", 1)[-1] == "article-id" and article_id.get("pub-id-type") == "doi":
            doi = _xml_text(article_id)
            break
    authors: list[str] = []
    for contrib in root.iter():
        if contrib.tag.rsplit("}", 1)[-1] != "contrib":
            continue
        if contrib.get("contrib-type") not in {None, "author"}:
            continue
        surname = _xml_text(_first_xml_element(contrib, "surname"))
        given = _xml_text(_first_xml_element(contrib, "given-names"))
        name = " ".join(value for value in (given, surname) if value)
        if not name:
            name = _xml_text(_first_xml_element(contrib, "string-name"))
        if not name:
            name = _xml_text(_first_xml_element(contrib, "collab"))
        if name:
            authors.append(name)

    markdown_lines = [f"# {title}", ""]
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": title, "text_level": 1, "page_idx": 0}
    ]
    if authors:
        author_line = ", ".join(dict.fromkeys(authors))
        markdown_lines.extend([author_line, ""])
        blocks.append({"type": "text", "text": author_line, "page_idx": 0})
    if doi:
        markdown_lines.extend([f"DOI: {doi}", ""])
        blocks.append({"type": "text", "text": f"DOI: {doi}", "page_idx": 0})

    license_statements: list[str] = []
    license_urls: list[str] = []
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        if local_name in {"license-p", "copyright-statement"}:
            statement = _xml_text(element)
            if statement and statement not in license_statements:
                license_statements.append(statement)
        if local_name == "ext-link":
            href = str(
                element.get("{http://www.w3.org/1999/xlink}href") or ""
            ).strip()
            if "creativecommons.org/licenses/" in href and href not in license_urls:
                license_urls.append(href)
    if license_statements or license_urls:
        markdown_lines.extend(["## License and reuse notice", ""])
        for statement in license_statements:
            markdown_lines.extend([statement, ""])
            blocks.append({"type": "text", "text": statement, "page_idx": 0})
        for license_url in license_urls:
            markdown_lines.extend([license_url, ""])
            blocks.append(
                {"type": "text", "text": license_url, "page_idx": 0}
            )

    abstract = _first_xml_element(root, "abstract")
    if abstract is not None:
        abstract_paragraphs = [
            _xml_text(element)
            for element in abstract.iter()
            if element.tag.rsplit("}", 1)[-1] == "p" and _xml_text(element)
        ]
        if abstract_paragraphs:
            markdown_lines.extend(["## Abstract", ""])
            blocks.append(
                {"type": "text", "text": "Abstract", "text_level": 1, "page_idx": 0}
            )
            for paragraph in abstract_paragraphs:
                markdown_lines.extend([paragraph, ""])
                blocks.append({"type": "text", "text": paragraph, "page_idx": 0})

    body = _first_xml_element(root, "body")

    def append_figure(figure: ET.Element) -> None:
        label = _xml_text(_first_xml_element(figure, "label"))
        caption = _xml_text(_first_xml_element(figure, "caption"))
        source_caption = " ".join(
            value for value in (label, caption) if value
        ).strip()
        figure_id = str(figure.get("id") or label or "").strip()
        graphic_hrefs: list[str] = []
        for element in figure.iter():
            if element.tag.rsplit("}", 1)[-1] != "graphic":
                continue
            href = str(
                element.get("{http://www.w3.org/1999/xlink}href") or ""
            ).strip()
            if href and href not in graphic_hrefs:
                graphic_hrefs.append(href)
        preferred_hrefs = sorted(
            graphic_hrefs,
            key=lambda href: (
                Path(href).suffix.lower() not in {".jpg", ".jpeg", ".png", ".tif", ".tiff"},
                href,
            ),
        )
        image_relative = ""
        image_url = ""
        graphic_href = ""
        for href in preferred_hrefs:
            filename = Path(urllib.parse.urlparse(href).path).name
            candidate_url = article_images.get(filename, "")
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", filename)
            relative = Path("images") / safe_name
            image_path = extracted_dir / relative
            archived_path = repository_assets.get(filename)
            if archived_path is not None and archived_path.is_file():
                image_path = archived_path
                relative = image_path.relative_to(extracted_dir)
            elif candidate_url:
                try:
                    if not image_path.exists():
                        download_repository_image(candidate_url, image_path, timeout)
                except Exception:
                    continue
            else:
                continue
            image_relative = relative.as_posix()
            image_url = (
                candidate_url
                or (
                    f"{repository_archive_url}#{urllib.parse.quote(filename)}"
                    if repository_archive_url
                    else ""
                )
            )
            graphic_href = href
            break
        source_locator = (
            f"{article_url.rstrip('/')}/figure/{urllib.parse.quote(figure_id)}/"
            if figure_id
            else article_url
        )
        block: dict[str, Any] = {
            "type": "image",
            "img_path": image_relative,
            "image_path": image_relative,
            "page_idx": None,
            "bbox": None,
            "img_caption": source_caption,
            "caption": source_caption,
            "repository_provider": "europe_pmc",
            "repository_id": pmcid,
            "repository_figure_id": figure_id,
            "repository_graphic_href": graphic_href,
            "repository_image_url": image_url,
            "source_locator": source_locator,
            "source_document": str(xml_path),
        }
        blocks.append(block)
        if source_caption:
            markdown_lines.extend([f"**{source_caption}**", ""])
        if image_relative:
            markdown_relative = (
                Path("..") / "extracted" / slug / image_relative
            ).as_posix()
            markdown_lines.extend(
                [f"![{source_caption or figure_id}]({markdown_relative})", ""]
            )

    def append_section(section: ET.Element, level: int = 2) -> None:
        title_element = next(
            (
                child
                for child in list(section)
                if child.tag.rsplit("}", 1)[-1] == "title"
            ),
            None,
        )
        section_title = _xml_text(title_element)
        if section_title:
            markdown_lines.extend([f"{'#' * min(level, 6)} {section_title}", ""])
            blocks.append(
                {
                    "type": "text",
                    "text": section_title,
                    "text_level": min(level - 1, 6),
                    "page_idx": 0,
                }
            )
        for child in list(section):
            child_name = child.tag.rsplit("}", 1)[-1]
            if child_name == "sec":
                append_section(child, level + 1)
            elif child_name == "fig":
                append_figure(child)
            elif child_name in {"p", "list", "disp-quote", "boxed-text"}:
                paragraph = _xml_text(child)
                if paragraph:
                    markdown_lines.extend([paragraph, ""])
                    blocks.append({"type": "text", "text": paragraph, "page_idx": 0})

    if body is not None:
        for child in list(body):
            child_name = child.tag.rsplit("}", 1)[-1]
            if child_name == "sec":
                append_section(child)
            elif child_name == "fig":
                append_figure(child)
            elif child_name == "p":
                paragraph = _xml_text(child)
                if paragraph:
                    markdown_lines.extend([paragraph, ""])
                    blocks.append({"type": "text", "text": paragraph, "page_idx": 0})

    xml_path.write_bytes(xml_bytes)
    markdown_path.write_text("\n".join(markdown_lines).rstrip() + "\n", encoding="utf-8")
    write_json(content_path, blocks)
    return xml_path, markdown_path


def default_target_relative(item: dict[str, Any]) -> str:
    def slugify(value: Any) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
        return slug or "paper"

    year = str(item.get("year") or "undated")
    title = slugify(item.get("title"))[:36]
    stable_suffix = slugify(item.get("paper_key") or item.get("doi") or title)[-12:]
    return f"chem_papers/web-imports/{year}-{title}-{stable_suffix}.pdf"


def compact_target_relative(item: dict[str, Any]) -> str:
    planned = str(item.get("target_pdf_path") or "")
    if planned and len(Path(planned).stem) <= 64:
        return planned
    return default_target_relative(item)


def mineru_slug_for_target(target: Path, pdf_root: Path) -> str:
    try:
        relative_stem = str(target.resolve().relative_to(pdf_root.resolve()).with_suffix(""))
    except ValueError:
        relative_stem = target.stem
    normalized = unicodedata.normalize("NFKD", relative_stem)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._/-]+", "-", ascii_text).strip("-._/")
    cleaned = cleaned.replace("/", "__")
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned.lower() or "document"


def expected_markdown_path(review_root: Path, target: Path) -> Path:
    slug = mineru_slug_for_target(target, review_root / "chem_papers")
    return review_root / "mineru-outputs" / "markdown" / f"{slug}.md"


def build_mineru_command(
    python_executable: str,
    mineru_script: Path,
    review_root: Path,
    targets: list[Path],
    batch_size: int,
) -> list[str]:
    command = [
        python_executable,
        str(mineru_script),
        "--input-dir",
        str(review_root / "chem_papers"),
        "--output-dir",
        str(review_root / "mineru-outputs"),
        "--batch-size",
        str(max(1, batch_size)),
    ]
    for target in targets:
        command.extend(["--pdf", str(target)])
    return command


def download_pdf(url: str, target: Path, timeout: int) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"unsupported PDF URL: {url}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "chemvellum-ingest/0.3",
            "Accept": "application/pdf,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temp.open("wb") as handle:
            first = response.read(8192)
            if not first.startswith(b"%PDF-"):
                raise ValueError("downloaded content is not a PDF")
            handle.write(first)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    temp.replace(target)
    return str(target)


def select_items(plan: dict[str, Any], paper_keys: list[str], limit: int) -> list[dict[str, Any]]:
    requested = set(paper_keys)
    rows = [
        row
        for row in plan.get("items") or []
        if isinstance(row, dict)
        and (
            row.get("action") == "download_then_mineru"
            or row.get("action") == "ingest_repository_full_text"
            or (row.get("action") == "locate_pdf" and row.get("open_access_full_text_url"))
            or (
                bool(requested)
                and row.get("action") == "use_local"
                and row.get("target_pdf_path")
            )
        )
        and (not requested or str(row.get("paper_key")) in requested)
    ]
    # Explicit paper keys are already a bounded, intentional selection. Applying
    # the general safety limit here silently drops requested papers after the
    # first N rows and makes batch imports look successful while incomplete.
    return rows[:limit] if limit > 0 and not requested else rows


def run_command(command: list[str]) -> tuple[int, str]:
    result = subprocess.run(command, text=True, encoding="utf-8", errors="replace")
    return result.returncode, " ".join(command)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download selected OA discovery papers, parse them with MinerU, and append metadata."
    )
    parser.add_argument("--review-root", default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--paper-key", action="append", default=[])
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument(
        "--all-available",
        action="store_true",
        help=(
            "Import every plan row with a direct PDF or licensed full-text page. "
            "Use after screening every row for relevance; otherwise select rows "
            "with repeated --paper-key."
        ),
    )
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument(
        "--mineru-batch-size",
        type=int,
        default=10,
        help="Number of explicitly selected PDFs per MinerU batch request. Default: 10.",
    )
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Stop after PDF download; do not call MinerU or metadata preparation.",
    )
    parser.add_argument(
        "--skip-metadata",
        action="store_true",
        help="Run MinerU but do not append the parsed papers to managed metadata yet.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    review_root = Path(args.review_root).resolve()
    discovery_dir = review_root / "review-projects" / args.project_id / "00_discovery"
    in_progress_path = discovery_dir / ".discovery_in_progress.json"
    if in_progress_path.exists():
        marker = read_json(in_progress_path)
        raise SystemExit(
            "Discovery is still in progress for run "
            f"{marker.get('discovery_run_id') or 'unknown'}; wait for a complete plan before ingestion."
        )
    plan_path = discovery_dir / "external_ingest_plan.json"
    if not plan_path.exists():
        raise SystemExit(f"Missing external ingest plan: {plan_path}")
    plan = read_json(plan_path)
    selected_path = discovery_dir / "selected_discovery_results.json"
    if selected_path.exists():
        selected = read_json(selected_path)
        plan_run = str(plan.get("discovery_run_id") or "")
        selected_run = str(selected.get("discovery_run_id") or "")
        if plan_run and selected_run and plan_run != selected_run:
            raise SystemExit(
                "Discovery outputs come from different runs; rerun discovery before ingestion."
            )
    hydrate_plan_bibliography(discovery_dir, plan)
    selection_limit = 0 if args.all_available else args.limit
    items = select_items(plan, args.paper_key, selection_limit)
    if not items:
        raise SystemExit("No downloadable external papers matched the selection.")

    metadata_before = load_managed_metadata(review_root)
    mineru_script = (
        review_root
        / "skills"
        / "mineru-precise-parse-chemvellum"
        / "scripts"
        / "parse_chemvellum_pdfs.py"
    )
    metadata_script = review_root / "skills" / "review-metadata-prep" / "scripts" / "prepare_metadata.py"
    receipt: dict[str, Any] = {
        "project_id": args.project_id,
        "discovery_run_id": plan.get("discovery_run_id"),
        "selected_paper_keys": [
            str(item.get("paper_key") or "") for item in items
        ],
        "started_at": utc_now(),
        "download_only": args.download_only,
        "all_available": args.all_available,
        "selected_count": len(items),
        "mineru_batch_size": max(1, args.mineru_batch_size),
        "phase": "downloading",
        "items": [],
        "metadata_status": "not_run",
        "candidate_update": "not_run",
    }
    parsed_any = False
    failures = 0
    targets_by_key: dict[str, Path] = {}
    for item in items:
        planned_target_relative = str(item.get("target_pdf_path") or "")
        target_relative = compact_target_relative(item)
        record = {
            "paper_key": item.get("paper_key"),
            "title": item.get("title"),
            "source_page_url": item.get("open_access_full_text_url"),
            "source_url": item.get("open_access_pdf_url"),
            "target_pdf_path": target_relative,
            "download_status": "pending",
            "mineru_status": "not_run",
            "promotion_status": "not_run",
        }
        try:
            item["target_pdf_path"] = target_relative
            target = safe_target(review_root, target_relative)
            repository_url = str(item.get("repository_full_text_url") or "").strip()
            if (
                repository_url
                and item.get("repository_provider") == "europe_pmc"
                and item.get("repository_format") == "jats_xml"
            ):
                pmcid = str(item.get("repository_id") or "").strip().upper()
                article_url = str(item.get("open_access_full_text_url") or "").strip()
                if not article_url and pmcid:
                    article_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
                xml_path = target.with_suffix(".jats.xml")
                markdown_path = expected_markdown_path(review_root, target)
                if (
                    xml_path.exists()
                    and markdown_path.exists()
                    and not args.force_download
                ):
                    record["download_status"] = "existing_jats"
                else:
                    xml_path, markdown_path = download_europe_pmc_jats(
                        article_url,
                        target,
                        review_root,
                        args.timeout,
                    )
                    record["download_status"] = "downloaded_jats"
                record["repository_format"] = "jats_xml"
                record["repository_source_path"] = str(xml_path)
                record["expected_markdown_path"] = str(markdown_path)
                record["source_url"] = repository_url
                record["repository_provider"] = "europe_pmc"
                targets_by_key[str(item.get("paper_key"))] = target
            else:
                source_url = str(item.get("open_access_pdf_url") or "")
                if not source_url:
                    source_url = resolve_pdf_url(
                        str(item.get("open_access_full_text_url") or ""),
                        args.timeout,
                    )
                    record["resolved_pdf_url"] = source_url
                    item["open_access_pdf_url"] = source_url
                record["source_url"] = source_url
                if planned_target_relative and planned_target_relative != target_relative:
                    planned_target = safe_target(review_root, planned_target_relative)
                    if planned_target.exists() and not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        planned_target.replace(target)
                        record["compacted_from_target_pdf_path"] = planned_target_relative
                if target.exists() and not args.force_download:
                    record["download_status"] = "existing"
                else:
                    download_pdf(source_url, target, args.timeout)
                    record["download_status"] = "downloaded"
                targets_by_key[str(item.get("paper_key"))] = target
        except Exception as primary_exc:
            record["primary_error"] = f"{type(primary_exc).__name__}: {primary_exc}"
            try:
                fallback_page_url = europe_pmc_article_url(item.get("doi"), args.timeout)
                fallback_pdf_url = resolve_pdf_url(fallback_page_url, args.timeout)
                target = safe_target(review_root, target_relative)
                try:
                    download_pdf(fallback_pdf_url, target, args.timeout)
                    record["download_status"] = "downloaded"
                    item["open_access_pdf_url"] = fallback_pdf_url
                    record["source_url"] = fallback_pdf_url
                except Exception as pdf_exc:
                    xml_path, markdown_path = download_europe_pmc_jats(
                        fallback_page_url,
                        target,
                        review_root,
                        args.timeout,
                    )
                    record["download_status"] = "downloaded_jats"
                    record["repository_format"] = "jats_xml"
                    record["repository_source_path"] = str(xml_path)
                    record["expected_markdown_path"] = str(markdown_path)
                    record["repository_pdf_error"] = f"{type(pdf_exc).__name__}: {pdf_exc}"
                    item["open_access_pdf_url"] = ""
                    item["repository_full_text_url"] = (
                        f"https://www.ebi.ac.uk/europepmc/webservices/rest/"
                        f"{fallback_page_url.rstrip('/').rsplit('/', 1)[-1]}/fullTextXML"
                    )
                    record["source_url"] = item["repository_full_text_url"]
                record["fallback_provider"] = "europe_pmc"
                record["fallback_page_url"] = fallback_page_url
                item["open_access_full_text_url"] = fallback_page_url
                item["target_pdf_path"] = target_relative
                targets_by_key[str(item.get("paper_key"))] = target
            except Exception as fallback_exc:
                record["fallback_error"] = f"{type(fallback_exc).__name__}: {fallback_exc}"
                record["error"] = (
                    f"primary={record['primary_error']}; "
                    f"europe_pmc={record['fallback_error']}"
                )
                record["download_status"] = "failed"
                failures += 1
        receipt["items"].append(record)
        receipt["finished_at"] = utc_now()
        receipt["failure_count"] = failures
        write_ingest_receipt(
            discovery_dir / "external_ingest_receipt.json",
            receipt,
        )

    write_json(plan_path, plan)
    receipt_by_key = {
        str(row.get("paper_key")): row
        for row in receipt["items"]
        if isinstance(row, dict) and row.get("paper_key")
    }
    if not args.download_only:
        receipt["phase"] = "parsing"
        parse_targets: list[Path] = []
        for paper_key, target in targets_by_key.items():
            record = receipt_by_key[paper_key]
            markdown_path = expected_markdown_path(review_root, target)
            record["expected_markdown_path"] = str(markdown_path)
            if markdown_path.exists():
                record["mineru_status"] = "completed"
                record["mineru_reused"] = True
                parsed_any = True
            else:
                parse_targets.append(target)
        if parse_targets:
            command_parts = build_mineru_command(
                sys.executable,
                mineru_script,
                review_root,
                parse_targets,
                args.mineru_batch_size,
            )
            code, command = run_command(command_parts)
            receipt["mineru_command"] = command
            receipt["mineru_process_exit_code"] = code
            for paper_key, target in targets_by_key.items():
                record = receipt_by_key[paper_key]
                if record.get("mineru_status") == "completed":
                    continue
                record["mineru_command"] = command
                if expected_markdown_path(review_root, target).exists():
                    record["mineru_status"] = "completed"
                    parsed_any = True
                else:
                    record["mineru_status"] = f"failed:{code}"
                    failures += 1
        receipt["finished_at"] = utc_now()
        receipt["failure_count"] = failures
        write_ingest_receipt(
            discovery_dir / "external_ingest_receipt.json",
            receipt,
        )

    if parsed_any and not args.skip_metadata and not args.download_only:
        receipt["phase"] = "metadata"
        code, command = run_command(
            [
                sys.executable,
                str(metadata_script),
                "--review-root",
                str(review_root),
                "--mineru-output",
                str(review_root / "mineru-outputs"),
                "--pdf-root",
                str(review_root / "chem_papers"),
                "--discover-from-pdf-root",
                "--append-registry",
            ]
            + [
                value
                for target in targets_by_key.values()
                for value in ("--only-slug", expected_markdown_path(review_root, target).stem)
            ]
        )
        receipt["metadata_command"] = command
        receipt["metadata_status"] = "completed" if code == 0 else f"failed:{code}"
        failures += int(code != 0)
        if code == 0:
            metadata_after = load_managed_metadata(review_root)
            receipt["reconciled_existing_papers"] = []
            new_ids = set(metadata_after) - set(metadata_before)
            plan_by_key = {
                str(row.get("paper_key")): row
                for row in plan.get("items") or []
                if isinstance(row, dict) and row.get("paper_key")
            }
            receipt_by_key = {
                str(row.get("paper_key")): row
                for row in receipt["items"]
                if isinstance(row, dict) and row.get("paper_key")
            }
            promotions: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
            for item in items:
                record = receipt_by_key.get(str(item.get("paper_key")))
                if not record or record.get("mineru_status") != "completed":
                    continue
                new_metadata = {
                    paper_id: metadata_after[paper_id]
                    for paper_id in new_ids
                    if paper_id in metadata_after
                }
                paper_id, metadata_row = match_managed_paper(item, new_metadata, new_ids)
                if not paper_id or not metadata_row:
                    record["promotion_status"] = "unresolved_metadata_identity"
                    failures += 1
                    continue
                metadata_row, reconciled_fields = reconcile_bibliographic_metadata(
                    review_root,
                    paper_id,
                    metadata_row,
                    item,
                )
                metadata_after[paper_id] = metadata_row
                record["local_paper_id"] = paper_id
                record["promotion_status"] = "added_to_managed_library"
                record["bibliographic_reconciled_fields"] = reconciled_fields
                plan_item = plan_by_key.get(str(item.get("paper_key")))
                if plan_item is not None:
                    plan_item.update(
                        {
                            "local_paper_id": paper_id,
                            "action": "use_local",
                            "next_step": f"Read managed paper {paper_id} against the review question.",
                        }
                    )
                promotions.append((item, paper_id, metadata_row))
            try:
                promoted_ids = add_promotions_to_candidates(discovery_dir, promotions)
                receipt["promoted_paper_ids"] = promoted_ids
                receipt["candidate_update"] = (
                    "completed" if len(promoted_ids) == len(promotions) else "partial"
                )
            except Exception as exc:
                receipt["candidate_update"] = f"failed:{type(exc).__name__}"
                receipt["candidate_update_error"] = str(exc)
                failures += 1
            remaining_downloads = sum(
                isinstance(row, dict) and row.get("action") == "download_then_mineru"
                for row in plan.get("items") or []
            )
            remaining_repository_imports = sum(
                isinstance(row, dict)
                and row.get("action") == "ingest_repository_full_text"
                for row in plan.get("items") or []
            )
            remaining_importable = sum(
                isinstance(row, dict)
                and row.get("action") != "use_local"
                and bool(
                    row.get("open_access_pdf_url")
                    or row.get("open_access_full_text_url")
                    or row.get("repository_full_text_url")
                )
                for row in plan.get("items") or []
            )
            plan["downloadable_count"] = remaining_downloads
            plan["repository_ingestible_count"] = remaining_repository_imports
            plan["importable_count"] = remaining_importable
            plan["status"] = "ready" if remaining_importable else "ingested_candidates_available"
            write_json(plan_path, plan)
    elif args.download_only:
        receipt["metadata_status"] = "download_only"
    elif args.skip_metadata:
        receipt["metadata_status"] = "skipped"

    receipt["phase"] = "finished"
    receipt["finished_at"] = utc_now()
    receipt["failure_count"] = failures
    receipt["remaining_importable_count"] = int(
        plan.get("importable_count") or 0
    )
    receipt["literature_set_guidance"] = (
        "Treat this batch as an orientation and working set. Use reading to "
        "find the direct primary support needed for central claims and major "
        "comparisons. Continue targeted discovery while material gaps remain; "
        "the set may be mature when new searches mainly repeat approaches and "
        "evidence already understood. For a broad comprehensive review, roughly "
        "40 genuinely relevant cited sources is a useful scale cue; recheck "
        "coverage when the emerging bibliography is much smaller, while letting "
        "the actual scope and evidence determine the final number."
    )
    write_ingest_receipt(
        discovery_dir / "external_ingest_receipt.json",
        receipt,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
