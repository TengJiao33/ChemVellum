#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import ast
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
TRAILING_FOOTNOTE_MARKER_RE = re.compile(r"\s*[†‡§¶#]+\s*$")
COPYRIGHT_AUTHOR_RE = re.compile(
    r"(?:©|\bcopyright\b|\bthe author\(s\)\b|\ball rights reserved\b|"
    r"\bcreative commons\b)",
    re.I,
)
INLINE_LETTER_HYPHEN_RE = re.compile(r"\b([NOSP])\s+-\s*(?=[a-z])")

JOURNAL_HINTS = [
    "Angewandte Chemie International Edition",
    "Angew. Chem. Int. Ed.",
    "Advanced Synthesis & Catalysis",
    "Adv. Synth. Catal.",
    "Tetrahedron Letters",
    "Tetrahedron",
    "European Journal of Organic Chemistry",
    "Eur. J. Org. Chem.",
    "Organic Letters",
    "Journal of Organic Chemistry",
    "Chemical Communications",
    "Green Chemistry",
    "Chemical Science",
]

JOURNAL_FILENAME_ALIASES = [
    ("angew chem int ed", "Angew. Chem. Int. Ed."),
    ("j am chem soc", "J. Am. Chem. Soc."),
    ("journal of organic chemistry", "J. Org. Chem."),
    ("j org chem", "J. Org. Chem."),
    ("organic letters", "Org. Lett."),
    ("org lett", "Org. Lett."),
    ("european journal of organic chemistry", "Eur. J. Org. Chem."),
    ("eur j org chem", "Eur. J. Org. Chem."),
    ("nature communications", "Nat. Commun."),
    ("nat commun", "Nat. Commun."),
    ("organic chemistry frontiers", "Org. Chem. Front."),
    ("org chem front", "Org. Chem. Front."),
    ("chemical communications", "Chem. Commun."),
    ("chem commun", "Chem. Commun."),
    ("green chemistry", "Green Chemistry"),
    ("chemistry a european journal", "Chem. Eur. J."),
]

STRUCTURED_TAG_KEYS = [
    "product",
    "substrate",
    "catalyst_or_method",
    "organometallic_partner",
    "ligand_or_chiral_source",
    "leaving_group",
    "reaction_type",
    "document_scope",
]


class RegistryFileLock:
    """Cross-process advisory lock for registry allocation and replacement."""

    def __init__(self, path: Path, timeout: float = 30.0) -> None:
        self.path = path
        self.timeout = timeout
        self.handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.handle = handle
                return
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError(
                        f"Timed out waiting for metadata registry lock: {self.path}"
                    )
                time.sleep(0.05)

    def release(self) -> None:
        handle = self.handle
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self.handle = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_text(text: str) -> str:
    text = text.replace("\u00ad", "")
    text = re.sub(r"\s+", " ", text)
    text = text.replace(" .", ".").replace(" ,", ",").strip()
    return text


def clean_title_text(text: str) -> str:
    text = clean_text(text)
    text = TRAILING_FOOTNOTE_MARKER_RE.sub("", text).strip()
    return INLINE_LETTER_HYPHEN_RE.sub(r"\1-", text)


def clean_author_name(text: str) -> str:
    name = clean_text(text)
    return "" if COPYRIGHT_AUTHOR_RE.search(name) else name


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_classification_rules(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    labels = {key: ["not specified"] for key in STRUCTURED_TAG_KEYS}
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rules_node = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "rules":
                    rules_node = node.value
                    break
        if rules_node is not None:
            break
    if rules_node is None:
        return labels
    for item in ast.literal_eval(rules_node):
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        label, category = str(item[0]).strip(), str(item[1]).strip()
        if category in labels and label:
            labels[category].append(label)
    return {key: dedupe(value) for key, value in labels.items()}


def classification_rules_prompt(labels: dict[str, list[str]]) -> str:
    if not labels:
        return (
            "Optional chemistry descriptors. For each category, use a concise phrase "
            "supported by the supplied paper, or `not specified`. These descriptors "
            "aid retrieval and do not define the review topic."
        )
    lines = [
        "Project-supplied optional classification labels. For each category, output exactly one label from its list.",
        "Use `not specified` only when no listed label is supported by the supplied paper evidence.",
    ]
    for key in STRUCTURED_TAG_KEYS:
        lines.append(f"\n{key}:")
        for label in labels.get(key, ["not specified"]):
            lines.append(f"- {label}")
    return "\n".join(lines)


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "paper"


def scored(value: Any, source: str, confidence: float) -> dict[str, Any]:
    return {
        "value": value,
        "source": source,
        "confidence": round(float(confidence), 3),
        "human_checked": False,
    }


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Verify the exact bytes that will replace the destination. This catches
    # interrupted or incorrectly encoded writes at the producer boundary.
    verified = json.loads(tmp.read_text(encoding="utf-8"))
    if verified != data:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"metadata JSON write verification failed: {path}")
    tmp.replace(path)


def portable_path(review_root: Path, path: Path | str | None) -> str | None:
    if path is None or not str(path).strip():
        return None
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(review_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def iter_jobs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for job in manifest.get("completed", []):
        if isinstance(job, dict):
            jobs.append(job)
    if jobs:
        return jobs
    for batch in manifest.get("batches", []):
        for job in batch.get("jobs", []):
            if isinstance(job, dict) and job.get("state") == "done":
                jobs.append(job)
    return jobs


def jobs_from_pdf_root(pdf_root: Path, mineru_output: Path) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    discovered_slugs: set[str] = set()
    for index, pdf_path in enumerate(sorted(pdf_root.rglob("*.pdf")), start=1):
        relative_stem = str(pdf_path.relative_to(pdf_root).with_suffix(""))
        base_slug = slugify_mineru(relative_stem)
        seen[base_slug] = seen.get(base_slug, 0) + 1
        slug = base_slug if seen[base_slug] == 1 else f"{base_slug}-{seen[base_slug]:02d}"
        extracted_dir = mineru_output / "extracted" / slug
        markdown_copy = mineru_output / "markdown" / f"{slug}.md"
        full_md = extracted_dir / "full.md"
        if not markdown_copy.exists() and not full_md.exists():
            continue
        discovered_slugs.add(slug)
        jobs.append(
            {
                "pdf_name": pdf_path.name,
                "relative_pdf_path": str(pdf_path.relative_to(pdf_root)),
                "slug": slug,
                "data_id": f"{index:03d}-{slug}"[:96],
                "state": "done",
                "err_msg": "",
                "raw_zip": str(mineru_output / "raw_zips" / f"{slug}.zip"),
                "extracted_dir": str(extracted_dir),
                "full_md": str(full_md),
                "markdown_copy": str(markdown_copy),
            }
        )
    markdown_dir = mineru_output / "markdown"
    xml_by_slug = {
        slugify_mineru(str(xml_path.relative_to(pdf_root))[: -len(".jats.xml")]): xml_path
        for xml_path in pdf_root.rglob("*.jats.xml")
    }
    for markdown_copy in sorted(markdown_dir.glob("*.md")):
        slug = markdown_copy.stem
        if slug in discovered_slugs:
            continue
        extracted_dir = mineru_output / "extracted" / slug
        source_xml = xml_by_slug.get(slug)
        jobs.append(
            {
                "pdf_name": None,
                "relative_pdf_path": None,
                "source_xml": str(source_xml) if source_xml else None,
                "slug": slug,
                "data_id": f"md-{slug}"[:96],
                "state": "done",
                "err_msg": "",
                "raw_zip": None,
                "extracted_dir": str(extracted_dir),
                "full_md": None,
                "markdown_copy": str(markdown_copy),
            }
        )
    return jobs


def slugify_mineru(value: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._/-]+", "-", ascii_text).strip("-._/")
    cleaned = cleaned.replace("/", "__")
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned.lower() or "document"


def read_registry_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def max_paper_number(rows: list[dict[str, Any]]) -> int:
    max_number = 0
    for row in rows:
        paper_id = str(row.get("paper_id") or "")
        match = re.fullmatch(r"P(\d+)", paper_id)
        if match:
            max_number = max(max_number, int(match.group(1)))
    return max_number


def registry_key(row: dict[str, Any], review_root: Path | None = None) -> str:
    for field in ("source_pdf", "markdown_path"):
        value = str(row.get(field) or "").strip()
        if not value:
            continue
        path = Path(value)
        if review_root is not None and not path.is_absolute():
            path = review_root / path
        try:
            return os.path.normcase(str(path.resolve()))
        except OSError:
            return os.path.normcase(str(path.absolute()))
    slug = str(row.get("slug") or "").strip()
    if slug:
        return f"slug:{slug}"
    paper_id = str(row.get("paper_id") or "").strip()
    return f"paper_id:{paper_id}" if paper_id else ""


def content_list_path(extracted_dir: Path) -> Path | None:
    candidates = sorted(extracted_dir.glob("*_content_list.json"))
    return candidates[0] if candidates else None


def load_blocks(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    data = read_json(path)
    return data if isinstance(data, list) else []


def block_texts(blocks: list[dict[str, Any]], max_page: int = 1) -> list[str]:
    out: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        page_index = block.get("page_idx")
        if not isinstance(page_index, int):
            page_index = 999
        if page_index > max_page:
            continue
        if block.get("type") not in {"text", "list", "table", "image", "chart"}:
            continue
        text = block.get("text") or block.get("content") or ""
        captions = block.get("image_caption") or []
        if isinstance(captions, list):
            text = " ".join([text] + [str(c) for c in captions])
        text = clean_text(str(text))
        if text:
            out.append(text)
    return out


def markdown_head(path: Path | None, chars: int = 14000) -> str:
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")[:chars]


def first_heading(md: str) -> str | None:
    for line in md.splitlines():
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            title = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", title)
            title = re.sub(r"\$([^$]+)\$", r"\1", title)
            return clean_title_text(title)
    return None


def extract_title(blocks: list[dict[str, Any]], md: str, slug: str) -> dict[str, Any]:
    heading = first_heading(md)
    if heading and len(heading) > 20:
        return scored(heading, "markdown_first_h1", 0.88)
    for block in blocks[:12]:
        text = clean_title_text(str(block.get("text") or ""))
        if block.get("text_level") == 1 and len(text) > 20 and not looks_like_section_heading(text):
            return scored(text, "content_list_text_level_1", 0.86)
    return scored(slug.replace("-", " "), "slug_fallback", 0.35)


def looks_like_section_heading(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    return compact in {
        "abstract",
        "articleinfo",
        "keywords",
        "introduction",
        "references",
        "conclusion",
        "conclusions",
    }


def extract_authors(blocks: list[dict[str, Any]], title_value: str) -> dict[str, Any]:
    title_seen = False
    candidates: list[str] = []
    for block in blocks[:20]:
        text = clean_text(str(block.get("text") or ""))
        if not text:
            continue
        if clean_text(title_value)[:35] in text or text[:35] in clean_text(title_value):
            title_seen = True
            continue
        if title_seen:
            if text.lower().startswith("abstract:") or len(text) > 260:
                break
            if COPYRIGHT_AUTHOR_RE.search(text):
                continue
            if re.search(r"\b(college|university|institute|laboratory|department|school|china|usa|abstract|keywords|herein|given the)\b", text, re.I):
                break
            if "," in text or re.search(r"\b[A-Z][a-z]+ [A-Z][a-z]+", text):
                candidates.append(text)
    if not candidates:
        return scored([], "rule_not_found", 0.0)
    raw = " ".join(candidates)
    raw = re.sub(r"\\\*|\*|\ba,b\b|\ba\b|\bb\b|\bc\b|\d+|†|‡", "", raw)
    parts = [clean_text(p) for p in re.split(r"\s*,\s*|\s+ and \s+", raw) if clean_text(p)]
    authors = []
    for part in parts:
        part = re.sub(r"\s+[a-z](?:\s+[a-z])?$", "", part).strip()
        part = re.sub(r"\\$", "", part).strip()
        part = part.replace("\\", "")
        part = re.sub(r"^and\s+", "", part, flags=re.I).strip()
        part = clean_author_name(part)
        if 3 <= len(part) <= 80 and not re.search(r"\b(college|university|laboratory|key)\b", part, re.I):
            authors.append(part)
    authors = dedupe(authors)
    return scored(authors, "content_list_after_title", 0.74 if authors else 0.0)


def extract_keywords(blocks: list[dict[str, Any]], md: str) -> dict[str, Any]:
    texts = block_texts(blocks, max_page=1)
    keywords: list[str] = []
    for i, text in enumerate(texts[:60]):
        compact = re.sub(r"\s+", "", text).lower()
        if compact in {"keywords:", "keywords"}:
            for nxt in texts[i + 1 : i + 10]:
                if looks_like_section_heading(nxt) or re.search(r"\babstract\b", nxt, re.I):
                    break
                if 2 <= len(nxt) <= 90:
                    keywords.append(nxt.strip(" ;,."))
            break
    if not keywords:
        m = re.search(r"Keywords:\s*(.+?)(?:\n\s*#|\n\s*A\s*B\s*S\s*T\s*R\s*A\s*C\s*T)", md, re.I | re.S)
        if m:
            raw = m.group(1)
            keywords = [clean_text(x).strip(" ;,.") for x in re.split(r"\n|;|,", raw) if clean_text(x)]
    keywords = [kw for kw in dedupe(keywords) if 2 <= len(kw) <= 80]
    return scored(keywords[:12], "content_list_keywords_region", 0.86 if keywords else 0.0)


def extract_abstract(blocks: list[dict[str, Any]], md: str) -> dict[str, Any]:
    texts = block_texts(blocks, max_page=2)
    for i, text in enumerate(texts[:80]):
        compact = re.sub(r"\s+", "", text).lower()
        if compact in {"abstract", "abstract:"} or compact == "abstract":
            parts: list[str] = []
            for nxt in texts[i + 1 : i + 35]:
                if re.match(r"^\d+\.\s+[A-Z]", nxt) or re.search(r"\bintroduction\b", nxt, re.I):
                    break
                if len(nxt) > 30:
                    parts.append(nxt)
            abstract = clean_text(" ".join(parts))
            if len(abstract) > 150:
                return scored(abstract, "content_list_abstract_region", 0.84)
        if text.lower().startswith("abstract:"):
            abstract = clean_text(text.split(":", 1)[1])
            if len(abstract) > 150:
                return scored(abstract, "content_list_inline_abstract", 0.84)
    m = re.search(r"#\s*A\s*B\s*S\s*T\s*R\s*A\s*C\s*T\s*(.+?)(?:\n#\s*\d+\.|\n#\s*1\.|\n#\s*Introduction)", md, re.I | re.S)
    if m:
        abstract = clean_text(re.sub(r"\n+", " ", m.group(1)))
        if len(abstract) > 80:
            return scored(abstract, "markdown_abstract_heading", 0.82)
    m = re.search(r"\bAbstract:\s*(.+?)(?:\n\s*#\s*Introduction|\n\s*#|\n\n#)", md, re.I | re.S)
    if m:
        abstract = clean_text(re.sub(r"\n+", " ", m.group(1)))
        if len(abstract) > 80:
            return scored(abstract, "markdown_inline_abstract", 0.82)
    intro = extract_intro_work_summary(md)
    if intro:
        return scored(intro, "markdown_introduction_ending_summary", 0.72)
    title_idx = None
    author_idx = None
    affiliation_like_re = re.compile(
        r"\b("
        r"university|institute|laboratory|lab\b|department|school|academy|"
        r"state key laboratory|academy of sciences|college|hospital|center|centre|"
        r"road|street|avenue|lu\b|china|usa|p\.?\s*r\.?\s*china|"
        r"shanghai|beijing|dalian|guangzhou|nanjing|wuhan|chengdu"
        r")\b",
        re.I,
    )
    abstract_signal_re = re.compile(
        r"\b("
        r"herein|we report|we describe|we disclose|we present|we developed|we have developed|"
        r"we demonstrate|we herein report|this paper|this work|this study|"
        r"a method|an efficient method|a practical method|protocol|procedure|"
        r"approach|strategy|transformation|construction|formation|access to|"
        r"is described|is reported|is disclosed|has been developed|has been achieved|"
        r"provides|enable(?:s|d)?|furnish(?:es|ed)?|deliver(?:s|ed)?|using|via|"
        r"enantioselective|asymmetric|selective|stereoselective|regioselective|chemoselective|"
        r"cataly[sz]ed|synthesis|prepared|afforded|reaction|under mild conditions|"
        r"in good yields|with high ee|with excellent"
        r")\b",
        re.I,
    )
    for i, block in enumerate(blocks[:20]):
        text = clean_text(str(block.get("text") or ""))
        if block.get("text_level") == 1 and len(text) > 20 and not looks_like_section_heading(text):
            title_idx = i
            continue
        if title_idx is not None and author_idx is None and 5 <= len(text) <= 260:
            if re.search(r"\b[A-Z][a-z]+", text) and ("," in text or " and " in text):
                author_idx = i
                continue
        if author_idx is not None and i > author_idx:
            if block.get("type") == "text" and 100 <= len(text) <= 1600:
                if not re.search(r"\b(introduction|keywords|received|accepted|cite this)\b", text[:80], re.I):
                    if affiliation_like_re.search(text):
                        continue
                    if not abstract_signal_re.search(text):
                        continue
                    return scored(text, "content_list_first_paragraph_after_authors", 0.68)
    return scored("", "rule_not_found", 0.0)


def extract_intro_work_summary(md: str) -> str:
    intro_match = re.search(
        r"\n#\s*(?:\d+\.?\s*)?Introduction\s*(.+?)(?:\n#\s*(?:\d+\.?\s*)?[A-Z])",
        md,
        re.I | re.S,
    )
    if not intro_match:
        return ""
    intro = intro_match.group(1)
    intro = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", intro)
    intro = re.sub(r"\$([^$]+)\$", r"\1", intro)
    intro = clean_text(intro)
    if len(intro) < 200:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", intro)
    sentences = [clean_text(s) for s in sentences if clean_text(s)]
    if len(sentences) < 2:
        return ""
    tail = sentences[-5:]
    signal_re = re.compile(
        r"\b("
        r"herein|in this work|in this paper|in this study|we report|we describe|we disclose|"
        r"we present|we developed|we have developed|we demonstrate|to address this|"
        r"based on this|using this|this work|this paper"
        r")\b",
        re.I,
    )
    selected = [s for s in tail if signal_re.search(s)]
    if not selected:
        return ""
    summary = clean_text(" ".join(selected[-3:]))
    return summary if len(summary) > 120 else ""


def extract_year(md: str, pdf_name: str) -> dict[str, Any]:
    candidates = [int(m.group(0)) for m in YEAR_RE.finditer(pdf_name + "\n" + md[:8000])]
    candidates = [y for y in candidates if 1990 <= y <= 2035]
    if candidates:
        # Prefer recent years in filename/front matter.
        return scored(max(set(candidates), key=candidates.count), "filename_or_front_matter", 0.68)
    return scored(None, "rule_not_found", 0.0)


def extract_doi(md: str) -> dict[str, Any]:
    m = DOI_RE.search(md[:20000])
    if m:
        return scored(m.group(0).rstrip(").,;"), "markdown_regex", 0.9)
    return scored(None, "rule_not_found", 0.0)


def extract_journal(md: str, pdf_name: str) -> dict[str, Any]:
    front_matter = md[:8000]
    cite = re.search(r"Cite this:\s*([^,\n]+)", front_matter, re.I)
    if cite:
        return scored(clean_text(cite.group(1)), "cite_this_line", 0.7)
    how = re.search(r"How to cite:\s*([^,\n]+)", front_matter, re.I)
    if how:
        return scored(clean_text(how.group(1)), "how_to_cite_line", 0.7)
    # Match journal hints only in the filename. Searching arbitrary front-matter
    # text can mistake an affiliation such as "Laboratory of Green Chemistry"
    # for the journal title.
    normalized_filename = re.sub(r"[^a-z0-9]+", " ", pdf_name.lower()).strip()
    for alias, canonical in JOURNAL_FILENAME_ALIASES:
        if alias in normalized_filename:
            return scored(canonical, "known_filename_alias", 0.78)
    for hint in JOURNAL_HINTS:
        if hint.lower() in pdf_name.lower():
            return scored(hint, "known_journal_hint", 0.72)
    filename = Path(pdf_name).stem
    if " - " in filename:
        first = filename.split(" - ")[0].strip()
        if len(first) > 3:
            return scored(first, "filename_prefix", 0.55)
    return scored(None, "rule_not_found", 0.0)


def dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        item = clean_text(str(item))
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def sha256_file(path: Path | None) -> str | None:
    if not path or not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_llm_payload(
    base: dict[str, Any],
    blocks: list[dict[str, Any]],
    md_head: str,
    system_prompt: str,
    model: str,
    reasoning_effort: str = "",
    classification_labels: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    classification_labels = classification_labels or {}
    front_blocks = []
    for i, block in enumerate(blocks[:80]):
        text = clean_text(str(block.get("text") or block.get("content") or ""))
        if text:
            front_blocks.append(
                {
                    "block_id": i,
                    "type": block.get("type"),
                    "text_level": block.get("text_level"),
                    "page_idx": block.get("page_idx"),
                    "text": text[:1200],
                }
            )
    user_content = {
        "path_hints": {
            "slug": base["slug"],
            "pdf": base["source_paths"]["pdf"],
            "markdown": base["source_paths"]["markdown"],
        },
        "rule_extracted_initial_metadata": {
            k: base.get(k)
            for k in [
                "title",
                "authors",
                "year",
                "journal",
                "doi",
                "abstract",
                "structured_tags",
            ]
        },
        "classification_rules": classification_rules_prompt(classification_labels),
        "front_blocks": front_blocks,
        "markdown_head": md_head[:9000],
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "title",
            "authors",
            "year",
            "abstract",
            "structured_tags",
            "warnings",
        ],
        "properties": {
            "title": field_schema("string"),
            "authors": field_schema("array"),
            "year": field_schema("integer_or_null"),
            "abstract": field_schema("string"),
            "structured_tags": structured_tags_schema(classification_labels),
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
    }
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "paper_metadata_extraction",
                "schema": schema,
                "strict": True,
            }
        },
    }
    if reasoning_effort and reasoning_effort.lower() != "none":
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload


def field_schema(kind: str) -> dict[str, Any]:
    if kind == "array":
        value_schema: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
    elif kind == "integer_or_null":
        value_schema = {"type": ["integer", "null"]}
    elif kind == "string_or_null":
        value_schema = {"type": ["string", "null"]}
    else:
        value_schema = {"type": "string"}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["value", "source", "confidence", "human_checked"],
        "properties": {
            "value": value_schema,
            "source": {"type": "string"},
            "confidence": {"type": "number"},
            "human_checked": {"type": "boolean"},
        },
    }


def structured_tags_schema(classification_labels: dict[str, list[str]] | None = None) -> dict[str, Any]:
    classification_labels = classification_labels or {}
    properties: dict[str, Any] = {}
    for key in STRUCTURED_TAG_KEYS:
        allowed = classification_labels.get(key)
        properties[key] = (
            {"type": "string", "enum": allowed}
            if allowed
            else {"type": "string"}
        )
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["value", "source", "confidence", "human_checked"],
        "properties": {
            "value": {
                "type": "object",
                "additionalProperties": False,
                "required": STRUCTURED_TAG_KEYS,
                "properties": properties,
            },
            "source": {"type": "string"},
            "confidence": {"type": "number"},
            "human_checked": {"type": "boolean"},
        },
    }


def call_openai_responses(payload: dict[str, Any], api_key: str, base_url: str = "https://api.openai.com") -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/responses",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "chemvellum-metadata-prep/1.0",
        },
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data.get("output_text")
    if not text:
        parts: list[str] = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    parts.append(content["text"])
        text = "\n".join(parts)
    if not text:
        raise RuntimeError("OpenAI response did not contain output_text")
    return json.loads(text)


def merge_llm(base: dict[str, Any], llm: dict[str, Any]) -> dict[str, Any]:
    for key in [
        "title",
        "authors",
        "year",
        "abstract",
        "structured_tags",
    ]:
        if not isinstance(llm.get(key), dict):
            continue
        current = base.get(key, {})
        if isinstance(current, dict) and current.get("human_checked"):
            continue
        new_value = llm[key].get("value")
        new_conf = float(llm[key].get("confidence") or 0)
        old_conf = float(current.get("confidence") or 0)
        current_value = current.get("value") if isinstance(current, dict) else None
        schema_changed = key == "structured_tags" and (
            not isinstance(current_value, dict) or set(current_value) != set(STRUCTURED_TAG_KEYS)
        )
        if has_value(new_value) and (schema_changed or new_conf >= old_conf or not has_value(current_value)):
            base[key] = {
                "value": new_value,
                "source": llm[key].get("source") or "llm",
                "confidence": round(new_conf, 3),
                "human_checked": bool(llm[key].get("human_checked", False)),
            }
    if isinstance(base.get("structured_tags"), dict):
        apply_structured_tags_to_compat_fields(base)
    warnings = llm.get("warnings") or []
    if isinstance(warnings, list):
        base["quality"]["warnings"].extend(str(w) for w in warnings if str(w).strip())
    return base


def normalize_structured_tags(value: Any) -> dict[str, str]:
    tags: dict[str, str] = {}
    if isinstance(value, dict):
        for key in STRUCTURED_TAG_KEYS:
            raw = clean_text(str(value.get(key) or "not specified"))
            tags[key] = raw or "not specified"
    else:
        tags = {key: "not specified" for key in STRUCTURED_TAG_KEYS}
    return tags


def constrain_structured_tags(
    values: dict[str, str],
    classification_labels: dict[str, list[str]],
) -> dict[str, str]:
    constrained: dict[str, str] = {}
    for key in STRUCTURED_TAG_KEYS:
        allowed = classification_labels.get(key)
        value = clean_text(str(values.get(key) or "not specified")) or "not specified"
        constrained[key] = value if not allowed or value in allowed else "not specified"
    return constrained


def first_or_not_specified(items: list[str]) -> str:
    for item in items:
        item = clean_text(str(item))
        if item:
            return item
    return "not specified"


def structured_tag_values(meta: dict[str, Any]) -> dict[str, str]:
    field = meta.get("structured_tags")
    value = field.get("value") if isinstance(field, dict) else None
    return normalize_structured_tags(value)


def apply_structured_tags_to_compat_fields(meta: dict[str, Any]) -> None:
    for key in [
        "keywords",
        "llm_tags",
        "human_tags",
        "topic_category",
        "reaction_category",
        "mechanism_category",
        "application_category",
    ]:
        meta.pop(key, None)


def has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def update_quality(meta: dict[str, Any]) -> None:
    missing: list[str] = []
    warnings: list[str] = list(meta.get("quality", {}).get("warnings", []))
    for key in ["title", "abstract"]:
        if not has_value(meta.get(key, {}).get("value")):
            missing.append(key)
    for key in ["authors"]:
        if not has_value(meta.get(key, {}).get("value")):
            warnings.append(f"empty_{key}")
    for key in ["year"]:
        if not has_value(meta.get(key, {}).get("value")):
            missing.append(key)
    for key in ["journal", "doi"]:
        if not has_value(meta.get(key, {}).get("value")):
            warnings.append(f"missing_{key}")
    confidences = []
    for key in [
        "title",
        "authors",
        "year",
        "journal",
        "doi",
        "abstract",
        "structured_tags",
    ]:
        field = meta.get(key)
        if isinstance(field, dict):
            confidences.append(float(field.get("confidence") or 0))
    overall = sum(confidences) / len(confidences) if confidences else 0
    if float(meta.get("title", {}).get("confidence") or 0) < 0.75:
        warnings.append("low_confidence_title")
    if float(meta.get("abstract", {}).get("confidence") or 0) < 0.75:
        warnings.append("low_confidence_abstract")
    title_value = str(meta.get("title", {}).get("value") or "")
    author_values = meta.get("authors", {}).get("value") or []
    if isinstance(author_values, str):
        author_values = [author_values]
    if TRAILING_FOOTNOTE_MARKER_RE.search(title_value):
        warnings.append("title_has_trailing_footnote_marker")
    if INLINE_LETTER_HYPHEN_RE.search(title_value):
        warnings.append("title_has_inline_markup_spacing")
    if any(COPYRIGHT_AUTHOR_RE.search(str(value)) for value in author_values):
        warnings.append("authors_include_copyright_statement")
    meta["quality"] = {
        "missing_fields": dedupe(missing),
        "warnings": dedupe(warnings),
        "overall_confidence": round(overall, 3),
        "needs_human_check": bool(missing or warnings),
    }


def existing_metadata(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = read_json(path)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _jats_text(
    element: ET.Element | None,
    *,
    skip_xref_types: set[str] | None = None,
) -> str:
    if element is None:
        return ""
    skipped = skip_xref_types or set()
    parts: list[str] = []

    def visit(node: ET.Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in list(node):
            local_name = child.tag.rsplit("}", 1)[-1]
            skip_child = (
                local_name == "xref"
                and str(child.get("ref-type") or "").lower() in skipped
            )
            if not skip_child:
                visit(child)
            if child.tail:
                parts.append(child.tail)

    visit(element)
    return clean_text("".join(parts))


def _jats_first(root: ET.Element, local_name: str) -> ET.Element | None:
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == local_name:
            return element
    return None


def extract_jats_metadata(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        root = ET.fromstring(path.read_bytes())
    except (ET.ParseError, OSError):
        return {}
    title_element = _jats_first(root, "article-title")
    abstract_element = _jats_first(root, "abstract")
    abstract_paragraphs = (
        [
            _jats_text(element)
            for element in abstract_element.iter()
            if element.tag.rsplit("}", 1)[-1] == "p" and _jats_text(element)
        ]
        if abstract_element is not None
        else []
    )
    result: dict[str, Any] = {
        "title": clean_title_text(
            _jats_text(
                title_element,
                skip_xref_types={"fn", "author-notes", "corresp"},
            )
        ),
        "abstract": clean_text(" ".join(abstract_paragraphs)),
        "journal": _jats_text(_jats_first(root, "journal-title")),
    }
    authors: list[str] = []
    for contrib in root.iter():
        if contrib.tag.rsplit("}", 1)[-1] != "contrib":
            continue
        if contrib.get("contrib-type") not in {None, "author"}:
            continue
        surname = _jats_text(_jats_first(contrib, "surname"))
        given = _jats_text(_jats_first(contrib, "given-names"))
        name = " ".join(value for value in (given, surname) if value)
        if not name:
            name = _jats_text(_jats_first(contrib, "string-name"))
        if not name:
            name = _jats_text(_jats_first(contrib, "collab"))
        if name:
            cleaned_name = clean_author_name(name)
            if cleaned_name:
                authors.append(cleaned_name)
    result["authors"] = dedupe(authors)
    for article_id in root.iter():
        if article_id.tag.rsplit("}", 1)[-1] == "article-id" and article_id.get("pub-id-type") == "doi":
            result["doi"] = re.sub(
                r"^https?://(?:dx\.)?doi\.org/",
                "",
                _jats_text(article_id),
                flags=re.I,
            ).lower()
            break
    for pub_date in root.iter():
        if pub_date.tag.rsplit("}", 1)[-1] != "pub-date":
            continue
        year_text = _jats_text(_jats_first(pub_date, "year"))
        if year_text.isdigit() and len(year_text) == 4:
            result["year"] = int(year_text)
            break
    return result


def build_metadata(
    paper_id: str,
    job: dict[str, Any],
    pdf_path: Path | None,
    md_path: Path | None,
    content_path: Path | None,
    existing: dict[str, Any] | None,
    review_root: Path,
    classification_labels: dict[str, list[str]],
) -> tuple[dict[str, Any], list[dict[str, Any]], str, list[dict[str, Any]]]:
    slug = str(job.get("slug") or slugify(job.get("pdf_name") or paper_id))
    blocks = load_blocks(content_path)
    md = markdown_head(md_path)
    title = extract_title(blocks, md, slug)
    authors = extract_authors(blocks, title["value"])
    keywords = extract_keywords(blocks, md)
    abstract = extract_abstract(blocks, md)
    year = extract_year(md, job.get("pdf_name") or slug)
    doi = extract_doi(md)
    journal = extract_journal(md, job.get("pdf_name") or slug)
    source_xml = Path(job["source_xml"]).resolve() if job.get("source_xml") else None
    jats = extract_jats_metadata(source_xml)
    if jats.get("title") and float(title.get("confidence") or 0) < 0.95:
        title = scored(jats["title"], "jats_article_title", 0.99)
    if jats.get("authors"):
        authors = scored(jats["authors"], "jats_contrib_group", 0.99)
    if jats.get("abstract"):
        abstract = scored(jats["abstract"], "jats_abstract", 0.99)
    if jats.get("year"):
        year = scored(jats["year"], "jats_pub_date", 0.99)
    if jats.get("doi"):
        doi = scored(jats["doi"], "jats_article_id", 0.99)
    if jats.get("journal"):
        journal = scored(jats["journal"], "jats_journal_title", 0.99)
    structured_tags = {key: "not specified" for key in STRUCTURED_TAG_KEYS}
    pdf_hash = sha256_file(pdf_path)
    meta: dict[str, Any] = {
        "paper_id": paper_id,
        "slug": slug,
        "title": title,
        "authors": authors,
        "year": year,
        "journal": journal,
        "doi": doi,
        "abstract": abstract,
        "structured_tags": scored(
            structured_tags,
            "optional_enrichment_not_run",
            0.0,
        ),
        "source_paths": {
            "pdf": portable_path(review_root, pdf_path),
            "xml": portable_path(review_root, source_xml),
            "markdown": portable_path(review_root, md_path),
            "content_list": portable_path(review_root, content_path),
            "extracted_dir": portable_path(review_root, job.get("extracted_dir")),
        },
        "source_file": {
            "pdf_name": job.get("pdf_name"),
            "relative_pdf_path": job.get("relative_pdf_path"),
            "sha256": pdf_hash,
        },
        "extraction": {
            "mode": "rules",
            "model": None,
            "created_at": utc_now(),
            "inputs": {
                "manifest": portable_path(
                    review_root, review_root / "mineru-outputs" / "manifest.json"
                ),
                "content_blocks": len(blocks),
                "markdown_chars_used": min(len(md), 14000),
            },
            "notes": [],
        },
        "human_review": existing.get("human_review")
        if existing and isinstance(existing.get("human_review"), dict)
        else {
            "status": "not_reviewed",
            "reviewed_at": None,
            "reviewer": None,
            "notes": [],
        },
        "quality": {
            "missing_fields": [],
            "warnings": [],
            "overall_confidence": 0,
            "needs_human_check": True,
        },
    }
    apply_structured_tags_to_compat_fields(meta)
    if existing:
        preserve_human_checked_fields(meta, existing)
    update_quality(meta)
    registry_row = {
        "paper_id": paper_id,
        "slug": slug,
        "title": meta["title"]["value"],
        "authors": meta["authors"]["value"],
        "year": meta["year"]["value"],
        "journal": meta["journal"]["value"],
        "doi": meta["doi"]["value"],
        "source_pdf": meta["source_paths"]["pdf"],
        "markdown_path": meta["source_paths"]["markdown"],
        "content_list_path": meta["source_paths"]["content_list"],
        "metadata_path": portable_path(
            review_root,
            review_root / "review-library" / "metadata" / "papers" / f"{paper_id}.metadata.json",
        ),
        "parse_status": "done",
        "human_review_status": meta["human_review"]["status"],
        "needs_human_check": meta["quality"]["needs_human_check"],
    }
    return meta, blocks, md, [registry_row]


def preserve_human_checked_fields(meta: dict[str, Any], existing: dict[str, Any]) -> None:
    for key, old in existing.items():
        if key in {"paper_id", "slug", "source_paths", "source_file", "extraction", "quality"}:
            continue
        if isinstance(old, dict) and (
            old.get("human_checked") is True or old.get("source") == "external_discovery"
        ):
            meta[key] = old


def copy_references(skill_root: Path, review_root: Path) -> None:
    dest = review_root / "review-library" / "metadata" / "extraction_prompts"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ["metadata_extraction_system.md", "metadata_schema.json"]:
        src = skill_root / "references" / name
        if src.exists():
            (dest / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    review_root = Path(args.review_root).resolve()
    load_dotenv(review_root / ".env")
    mineru_output = Path(args.mineru_output).resolve()
    pdf_root = Path(args.pdf_root).resolve() if args.pdf_root else None
    skill_root = Path(__file__).resolve().parents[1]
    out_meta_dir = review_root / "review-library" / "metadata" / "papers"
    out_registry = review_root / "review-library" / "registry" / "papers.jsonl"
    out_meta_dir.mkdir(parents=True, exist_ok=True)
    out_registry.parent.mkdir(parents=True, exist_ok=True)
    copy_references(skill_root, review_root)
    manifest_path = mineru_output / "manifest.json"
    if args.discover_from_pdf_root:
        if not pdf_root:
            print("ERROR: --discover-from-pdf-root requires --pdf-root", file=sys.stderr)
            return 2
        jobs = jobs_from_pdf_root(pdf_root, mineru_output)
    else:
        if not manifest_path.exists():
            print(f"ERROR: missing MinerU manifest: {manifest_path}", file=sys.stderr)
            return 2
        manifest = read_json(manifest_path)
        jobs = iter_jobs(manifest)
    if args.only_slug:
        allowed_slugs = {str(value).strip() for value in args.only_slug if str(value).strip()}
        jobs = [job for job in jobs if str(job.get("slug") or "").strip() in allowed_slugs]
        missing_slugs = sorted(
            allowed_slugs - {str(job.get("slug") or "").strip() for job in jobs}
        )
        if missing_slugs:
            print(
                "ERROR: requested parsed sources were not found: " + ", ".join(missing_slugs),
                file=sys.stderr,
            )
            return 2
    if not jobs:
        print("ERROR: no parsed metadata jobs matched the requested sources", file=sys.stderr)
        return 2

    system_prompt = (skill_root / "references" / "metadata_extraction_system.md").read_text(encoding="utf-8")
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    model = args.model or os.environ.get("REVIEW_METADATA_MODEL", "gpt-5.4")
    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
    reasoning_effort = args.reasoning_effort or os.environ.get("REVIEW_METADATA_REASONING_EFFORT", "high")
    classification_labels = (
        load_classification_rules(Path(args.classification_rules).resolve())
        if args.classification_rules
        else {}
    )
    use_llm = bool(args.use_llm)
    if use_llm and not api_key:
        print("WARN: --use-llm was set but OPENAI_API_KEY is missing; using rules only.", file=sys.stderr)
        use_llm = False

    registry_lock = RegistryFileLock(
        out_registry.with_suffix(out_registry.suffix + ".lock")
    )
    registry_lock.acquire()
    atexit.register(registry_lock.release)
    existing_rows = read_registry_rows(out_registry) if args.append_registry else []
    existing_by_key = {
        registry_key(row, review_root): row
        for row in existing_rows
        if registry_key(row, review_root)
    }
    rows: list[dict[str, Any]] = []
    next_paper_number = max_paper_number(existing_rows) + 1
    for index, job in enumerate(jobs, start=1):
        slug = str(job.get("slug") or slugify(job.get("pdf_name") or f"paper-{index:03d}"))
        md_path = Path(job["markdown_copy"]).resolve() if job.get("markdown_copy") else None
        if not md_path or not md_path.exists():
            full_md = Path(job["full_md"]).resolve() if job.get("full_md") else None
            md_path = full_md if full_md and full_md.exists() else None
        extracted_dir = Path(job["extracted_dir"]).resolve() if job.get("extracted_dir") else mineru_output / "extracted" / slug
        cpath = content_list_path(extracted_dir)
        pdf_path = None
        if pdf_root and job.get("relative_pdf_path"):
            candidate = pdf_root / str(job["relative_pdf_path"])
            if candidate.exists():
                pdf_path = candidate.resolve()
        if not pdf_path:
            origin_candidates = sorted(extracted_dir.glob("*_origin.pdf"))
            if origin_candidates:
                pdf_path = origin_candidates[0].resolve()
        candidate_key = registry_key(
            {
                "source_pdf": str(pdf_path) if pdf_path else "",
                "markdown_path": str(md_path) if md_path else "",
                "slug": slug,
            },
            review_root,
        )
        existing_row = existing_by_key.get(candidate_key)
        if existing_row:
            paper_id = str(existing_row.get("paper_id"))
        else:
            paper_id = f"P{next_paper_number:03d}"
            next_paper_number += 1
        meta_path = out_meta_dir / f"{paper_id}.metadata.json"
        existing = existing_metadata(meta_path)
        meta, blocks, md, reg_rows = build_metadata(
            paper_id,
            job,
            pdf_path,
            md_path,
            cpath,
            existing,
            review_root,
            classification_labels,
        )
        if use_llm:
            try:
                payload = build_llm_payload(meta, blocks, md, system_prompt, model, reasoning_effort, classification_labels)
                llm_data = call_openai_responses(payload, api_key or "", base_url)
                merge_llm(meta, llm_data)
                meta["extraction"]["mode"] = "rules+llm"
                meta["extraction"]["model"] = model
                meta["extraction"]["notes"].append("llm_enhanced_metadata")
                update_quality(meta)
                if args.sleep_seconds:
                    time.sleep(args.sleep_seconds)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
                meta["extraction"]["notes"].append(f"llm_failed: {type(exc).__name__}: {exc}")
                meta["quality"]["warnings"].append("llm_failed")
                update_quality(meta)
        write_json(meta_path, meta)
        reg = reg_rows[0]
        reg.update(
            {
                "title": meta["title"]["value"],
                "authors": meta["authors"]["value"],
                "year": meta["year"]["value"],
                "journal": meta["journal"]["value"],
                "doi": meta["doi"]["value"],
                "human_review_status": meta["human_review"]["status"],
                "needs_human_check": meta["quality"]["needs_human_check"],
            }
        )
        rows.append(reg)
        print(f"{paper_id} {slug} metadata written")

    if args.append_registry:
        new_keys = {
            registry_key(row, review_root)
            for row in rows
            if registry_key(row, review_root)
        }
        rows = [
            row
            for row in existing_rows
            if registry_key(row, review_root) not in new_keys
        ] + rows
    tmp = out_registry.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    tmp.replace(out_registry)
    registry_lock.release()
    atexit.unregister(registry_lock.release)
    print(f"Wrote {len(rows)} papers to {out_registry}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare review paper metadata from MinerU outputs.")
    default_root = Path(__file__).resolve().parents[3]
    parser.add_argument("--review-root", default=str(default_root))
    parser.add_argument("--mineru-output", default=str(default_root / "mineru-outputs"))
    parser.add_argument("--pdf-root", default=str(default_root / "chem_papers"))
    parser.add_argument(
        "--discover-from-pdf-root",
        action="store_true",
        help="Discover parsed MinerU outputs by matching PDFs under --pdf-root to markdown/extracted outputs.",
    )
    parser.add_argument(
        "--append-registry",
        action="store_true",
        help="Append or update papers in the existing registry instead of replacing papers.jsonl.",
    )
    parser.add_argument(
        "--only-slug",
        action="append",
        default=[],
        help="Process only the named parsed source slug. Repeat for a bounded append-only import.",
    )
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument(
        "--classification-rules",
        default="",
        help=(
            "Optional project-specific Python rules file. Omit it for generic, "
            "free-text chemistry descriptors."
        ),
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--reasoning-effort", default="", choices=["", "none", "low", "medium", "high"])
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
