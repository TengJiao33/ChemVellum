#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import ast
import http.client
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from create_project import (
    archive_current_discovery,
    ensure_review_project,
    record_discovery_run,
)
from sciatlas_client import SciAtlasClient, load_config, papers_from_response


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def progress(message: str) -> None:
    """Emit a timestamped, immediately visible progress line.

    Discovery's machine-readable artifacts remain on disk and its short final
    summary remains on stdout. Long-running provider activity goes to stderr so
    callers can monitor the process without waiting for completion or parsing a
    partially written JSON document.
    """
    print(f"[discover {utc_now()}] {message}", file=sys.stderr, flush=True)


ProgressCallback = Callable[[str], None]


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:96] or "review-discovery"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def acquire_discovery_process_lock(project: Path, project_id: str) -> Path:
    """Prevent two discovery processes from mutating one project at once."""
    lock_path = project / ".discovery_process.json"
    payload = {
        "project_id": project_id,
        "pid": os.getpid(),
        "started_at": utc_now(),
        "status": "running",
    }
    for attempt in range(3):
        try:
            descriptor = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            try:
                owner = read_json(lock_path)
            except (OSError, json.JSONDecodeError):
                if attempt < 2:
                    time.sleep(0.1)
                    continue
                raise RuntimeError(
                    f"Discovery lock is unreadable: {lock_path}. Verify that no "
                    "discovery process is running before removing the stale lock."
                )
            owner_pid = int(owner.get("pid") or 0) if isinstance(owner, dict) else 0
            if process_is_running(owner_pid):
                raise RuntimeError(
                    f"Discovery is already running for {project_id} as PID "
                    f"{owner_pid}. Monitor that process instead of starting another."
                )
            lock_path.unlink(missing_ok=True)
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        return lock_path
    raise RuntimeError(f"Could not acquire discovery lock: {lock_path}")


def release_discovery_process_lock(lock_path: Path, pid: int | None = None) -> None:
    owner_pid = os.getpid() if pid is None else pid
    try:
        owner = read_json(lock_path)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return
    if isinstance(owner, dict) and int(owner.get("pid") or 0) == owner_pid:
        lock_path.unlink(missing_ok=True)


def split_keywords(raw: str) -> list[str]:
    return dedupe([x.strip() for x in re.split(r"[,;；\n]+", raw or "") if x.strip()])


def _markdown_contract(path: Path) -> dict[str, Any]:
    sections: dict[str, list[str]] = {}
    current = ""
    document_title = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("# ") and not line.startswith("## "):
            if not document_title:
                document_title = line[2:].strip()
            current = ""
        elif line.startswith("## "):
            current = re.sub(r"[^a-z0-9]+", "_", line[3:].strip().lower()).strip("_")
            sections.setdefault(current, [])
        elif current and line and not line.startswith("# "):
            sections[current].append(line)

    def section_lines(*names: str) -> list[str]:
        for name in names:
            if sections.get(name):
                return sections[name]
        return []

    def scalar(*names: str) -> str:
        return " ".join(
            line.lstrip("- ").strip() for line in section_lines(*names)
        ).strip()

    def items(*names: str) -> list[str]:
        lines = section_lines(*names)
        if not lines:
            return []
        grouped: list[str] = []
        current_item = ""
        saw_bullet = any(line.startswith(("- ", "* ")) for line in lines)
        for line in lines:
            if line.startswith(("- ", "* ")):
                if current_item:
                    grouped.append(current_item)
                current_item = line[2:].strip()
            elif saw_bullet and current_item:
                current_item = f"{current_item} {line.strip()}".strip()
            elif not saw_bullet:
                current_item = f"{current_item} {line.strip()}".strip()
        if current_item:
            grouped.append(current_item)
        return dedupe(grouped)

    manuscript_title = scalar("manuscript_title", "title") or document_title
    return {
        "topic": document_title,
        "manuscript_title": manuscript_title,
        "retrieval_query": scalar("retrieval_query", "search_query"),
        "review_profile": scalar("review_profile"),
        "central_question": scalar("central_question", "review_question"),
        "important_coverage": items("important_coverage", "coverage"),
        "inclusion_criteria": items("inclusion_criteria", "inclusion"),
        "exclusion_criteria": items("exclusion_criteria", "exclusion"),
        "suggested_keywords": items(
            "suggested_retrieval_keywords",
            "retrieval_keywords",
            "keywords",
        ),
    }


def load_topic_contract_file(raw_path: str) -> dict[str, Any]:
    if not raw_path:
        return {}
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"Topic contract file does not exist: {path}")
    if path.suffix.lower() == ".json":
        payload = read_json(path)
        if not isinstance(payload, dict):
            raise SystemExit(f"Topic contract JSON must contain an object: {path}")
        return payload
    if path.suffix.lower() in {".md", ".markdown"}:
        return _markdown_contract(path)
    raise SystemExit("--topic-contract-file must be JSON or Markdown")


def dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = re.sub(r"\s+", " ", str(value).strip())
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def field_value(field: Any, default: Any = None) -> Any:
    if isinstance(field, dict) and "value" in field:
        return field.get("value", default)
    return field if field is not None else default


def load_metadata(review_root: Path) -> dict[str, dict[str, Any]]:
    meta_dir = review_root / "review-library" / "metadata" / "papers"
    papers: dict[str, dict[str, Any]] = {}
    for path in sorted(meta_dir.glob("*.metadata.json")):
        try:
            meta = read_json(path)
        except Exception:
            continue
        pid = meta.get("paper_id")
        if pid:
            papers[pid] = meta
    return papers


# These are legacy metadata fields, not the vocabulary of the retrieval
# planner. Generic title/abstract/full-text retrieval must continue to work
# when a paper comes from a different domain and none of these fields applies.
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


def load_classification_rules(
    review_root: Path,
    topic: str = "",
) -> dict[str, dict[str, list[str]]]:
    labels = {key: {} for key in STRUCTURED_TAG_KEYS}
    paths = sorted(review_root.glob("*_classification_rules.py"))
    generic_path = review_root / "classification_rules.py"
    if generic_path.exists():
        paths.insert(0, generic_path)
    for path in paths:
        domain = path.stem.removesuffix("_classification_rules")
        if path != generic_path and domain and not contains_term(topic, domain):
            continue
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
            continue
        for item in ast.literal_eval(rules_node):
            if not isinstance(item, tuple) or len(item) < 3:
                continue
            label, category, aliases = str(item[0]).strip(), str(item[1]).strip(), item[2]
            if category in labels and label:
                labels[category][label] = [
                    str(alias).strip() for alias in aliases if str(alias).strip()
                ]
    return labels


def markdown_signal(
    meta: dict[str, Any],
    review_root: Path | None = None,
    max_chars: int = 12000,
) -> str:
    source_paths = meta.get("source_paths") or {}
    path = Path(str(source_paths.get("markdown") or ""))
    if review_root is not None and not path.is_absolute():
        path = review_root / path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]


def tokenize(text: str) -> list[str]:
    return dedupe([w.lower() for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'′\\-]*", text or "") if len(w) >= 3])


def contains_term(text: str, term: str) -> bool:
    """Match a token or phrase without allowing arbitrary substring hits."""
    text = re.sub(r"\bpoly\s*\(\s*([a-z0-9]+)", r"poly\1", text.lower())
    normalized_text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    normalized_term = re.sub(r"[^a-z0-9]+", " ", term.lower()).strip()
    if not normalized_term:
        return False
    return bool(re.search(rf"(?:^| ){re.escape(normalized_term)}(?: |$)", normalized_text))


QUERY_STOPWORDS = {
    "a", "about", "across", "an", "and", "are", "as", "at", "based", "between",
    "by", "can", "cannot", "determine", "direct", "do", "does", "effects",
    "explicit", "fairly", "families", "for", "from", "how", "in", "including",
    "into", "is", "of", "on", "or", "our", "paper", "ranked", "relevant",
    "review", "rules", "study", "studies", "such", "that", "the", "their",
    "these", "this", "through", "to", "toward", "towards", "using", "versus",
    "via", "what", "when", "where", "which", "with", "without",
}
TOPIC_ANCHOR_STOPWORDS = {
    "advance", "advances", "application", "applications", "derivative",
    "derivatives", "method", "methods", "recent", "review", "synthesis",
}


def compact_query(text: str, max_terms: int = 8) -> str:
    """Turn a contract phrase into a bounded provider query without a domain lexicon."""
    normalized = re.sub(r"\([^)]{80,}\)", " ", str(text or ""))
    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9+.'鈥瞈/-]*", normalized)
    kept: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.lower().strip("./")
        if not key or key in QUERY_STOPWORDS:
            continue
        if len(key) < 3 and not any(char.isdigit() for char in key):
            continue
        if key in seen:
            continue
        seen.add(key)
        kept.append(term.strip(".,;:"))
        if len(kept) >= max(max_terms, 1):
            break
    return " ".join(kept)


def topic_anchor_query(text: str, max_terms: int = 3) -> str:
    """Keep a few topic-defining terms for high-recall facet queries."""
    terms = compact_query(
        text,
        max_terms=max_terms + len(TOPIC_ANCHOR_STOPWORDS),
    ).split()
    specific = [
        term
        for term in terms
        if term.lower().strip("./") not in TOPIC_ANCHOR_STOPWORDS
    ]
    return " ".join((specific or terms)[:max(max_terms, 1)])


def facet_query_chunks(text: str, terms_per_query: int = 4) -> list[str]:
    """Split a dense coverage bullet so later method terms remain searchable."""
    terms = compact_query(text, max_terms=16).split()
    return [
        " ".join(terms[start:start + terms_per_query])
        for start in range(0, len(terms), terms_per_query)
        if terms[start:start + terms_per_query]
    ]


def _query_candidate(text: str, category: str, reason: str) -> dict[str, Any] | None:
    query = compact_query(text)
    if len(tokenize(query)) < 2:
        return None
    return {"keyword": query, "category": category, "reason": reason}


def infer_keywords(
    topic: str,
    user_keywords: list[str],
    topic_contract: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build a small, explainable query plan from any topic contract."""
    contract = topic_contract if isinstance(topic_contract, dict) else {}
    candidates: list[dict[str, Any]] = []
    title = str(contract.get("manuscript_title") or topic).strip()
    topic_anchor = topic_anchor_query(title, max_terms=3)
    title_parts = [
        part.strip()
        for part in re.split(r"[:;]", title)
        if len(tokenize(part)) >= 2
    ]
    core = _query_candidate(
        title_parts[0] if title_parts else topic,
        "core_topic",
        "compact core query from the declared topic",
    )
    if core:
        core["facet_group"] = "core_topic"
        candidates.append(core)
    for coverage_index, coverage in enumerate(contract.get("important_coverage") or []):
        for facet in facet_query_chunks(str(coverage)):
            row = _query_candidate(
                " ".join(part for part in (topic_anchor, facet) if part),
                "coverage",
                "coverage query anchored to the declared topic",
            )
            if row:
                row["facet_group"] = f"coverage_{coverage_index}"
                candidates.append(row)
    if len(candidates) < 3:
        central = str(contract.get("central_question") or "")
        for clause in re.split(r"[?;]|\.\s+|,\s+(?:and|or)\s+", central):
            row = _query_candidate(
                clause,
                "mechanism_or_outcome",
                "query from the central question",
            )
            if row:
                candidates.append(row)
            if len(candidates) >= 4:
                break
    if len(candidates) < 3:
        for criterion in contract.get("inclusion_criteria") or []:
            row = _query_candidate(
                str(criterion),
                "scope",
                "scope query from an inclusion criterion",
            )
            if row:
                candidates.append(row)
            if len(candidates) >= 3:
                break
    if len(candidates) < 2 and title_parts:
        for part in title_parts[1:]:
            row = _query_candidate(
                part,
                "coverage",
                "secondary title clause used to avoid a single-title query",
            )
            if row:
                candidates.append(row)
    return unique_keyword_dicts(candidates)


def unique_keyword_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = item["keyword"].lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def build_keyword_set(
    topic: str,
    user_keywords: list[str],
    topic_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    agent = infer_keywords(topic, user_keywords, topic_contract)
    merged: dict[str, dict[str, Any]] = {}
    for keyword_index, kw in enumerate(user_keywords):
        merged[kw.lower()] = {
            "keyword": kw,
            "category": classify_keyword(kw),
            "source": ["user"],
            "keep": True,
            "facet_group": f"user_{keyword_index}",
        }
    for item in agent:
        key = item["keyword"].lower()
        if key in merged:
            if "agent" not in merged[key]["source"]:
                merged[key]["source"].append("agent")
            if not merged[key].get("category"):
                merged[key]["category"] = item["category"]
        else:
            merged[key] = {
                "keyword": item["keyword"],
                "category": item["category"],
                "source": ["agent"],
                "keep": True,
                "reason": item.get("reason", ""),
                "facet_group": item.get("facet_group"),
            }
    warnings: list[str] = []
    if len(merged) < 3:
        warnings.append(
            "The topic contract yielded fewer than three distinct queries; inspect scope fields before relying on coverage."
        )
    return {
        "user_topic": topic,
        "user_keywords": user_keywords,
        "agent_keywords": agent,
        "merged_keywords": list(merged.values()),
        "query_plan": {
            "strategy": "topic_contract_dimensions",
            "query_count": len(merged),
            "warnings": warnings,
        },
        "created_at": utc_now(),
    }


def classify_keyword(keyword: str) -> str:
    low = keyword.lower()
    if any(x in low for x in ["review", "perspective", "guideline", "consensus"]):
        return "document_scope"
    if any(
        x in low
        for x in [
            "operando", "in situ", "spectroscopy", "microscopy", "tomography",
            "simulation", "modeling", "measurement", "evidence",
        ]
    ):
        return "method_or_evidence"
    if any(
        x in low
        for x in [
            "mechanism", "failure", "degradation", "transport", "kinetic",
            "impedance", "performance", "outcome",
        ]
    ):
        return "mechanism_or_outcome"
    return "user_query"


STRUCTURED_TAG_WEIGHTS = {
    "product": 5.0,
    "substrate": 5.0,
    "catalyst_or_method": 4.4,
    "organometallic_partner": 4.0,
    "ligand_or_chiral_source": 3.8,
    "leaving_group": 3.8,
    "reaction_type": 4.8,
    "document_scope": 1.5,
}


def structured_tag_text(meta: dict[str, Any], tag_key: str, classification_rules: dict[str, dict[str, list[str]]]) -> str:
    structured = field_value(meta.get("structured_tags"), {})
    if not isinstance(structured, dict):
        return ""
    value = str(structured.get(tag_key) or "")
    if value.strip().lower() == "not specified":
        return ""
    aliases = classification_rules.get(tag_key, {}).get(value, [])
    return " ".join([value] + aliases)


def match_score(term: str, text: str) -> float:
    if not term or not text:
        return 0.0
    low = text.lower()
    t = term.lower()
    if t in low:
        return 1.0
    tokens = tokenize(t)
    if not tokens:
        return 0.0
    hits = sum(1 for token in tokens if token in low)
    ratio = hits / len(tokens)
    if len(tokens) == 1:
        return 0.65 if hits else 0.0
    if ratio == 1.0:
        return 0.72
    if ratio >= 0.67 and len(tokens) >= 3:
        return 0.38
    return 0.0


def score_local_paper(
    meta: dict[str, Any],
    keyword: str,
    topic_terms: list[str],
    classification_rules: dict[str, dict[str, list[str]]],
    markdown_cache: dict[str, str] | None = None,
    review_root: Path | None = None,
) -> dict[str, Any]:
    matched_fields: list[str] = []
    matched_terms: list[str] = []
    reasons: list[str] = []
    raw = 0.0
    direct_raw = 0.0
    title = str(field_value(meta.get("title"), "") or "")
    abstract = str(field_value(meta.get("abstract"), "") or "")
    source_paths = meta.get("source_paths") or {}
    markdown_path = str(source_paths.get("markdown") or "")
    cache = markdown_cache if markdown_cache is not None else {}
    if markdown_path not in cache:
        cache[markdown_path] = (
            markdown_signal(meta, review_root) if markdown_path else ""
        )
    for field, text, weight in (
        ("title", title, 5.0),
        ("abstract", abstract, 3.2),
        ("full_text", cache.get(markdown_path, ""), 1.6),
    ):
        score = match_score(keyword, text)
        if score <= 0:
            continue
        contribution = score * weight
        raw += contribution
        direct_raw += contribution
        matched_fields.append(field)
        matched_terms.append(keyword)
        reasons.append(f"{field} matched query")
    for field, weight in STRUCTURED_TAG_WEIGHTS.items():
        text = structured_tag_text(meta, field, classification_rules)
        s = match_score(keyword, text)
        if s > 0:
            contribution = s * weight
            raw += contribution
            direct_raw += contribution
            matched_fields.append(field)
            matched_terms.append(keyword)
            reasons.append(f"structured_tags.{field} matched keyword")
        topic_hits = sum(1 for term in topic_terms if match_score(term, text) > 0)
        if topic_hits and s > 0:
            raw += min(topic_hits * 0.15, 0.9)
    year = field_value(meta.get("year"))
    normalized = min(round(raw / 8.0, 4), 1.0)
    if normalized >= 0.65:
        role = "core_candidate"
    elif normalized >= 0.35:
        role = "supporting_candidate"
    elif normalized >= 0.15:
        role = "background"
    else:
        role = "uncertain"
    return {
        "paper_id": meta.get("paper_id"),
        "title": title,
        "authors": field_value(meta.get("authors"), []),
        "year": year,
        "journal": field_value(meta.get("journal")),
        "doi": field_value(meta.get("doi")),
        "abstract": field_value(meta.get("abstract"), ""),
        "structured_tags": field_value(meta.get("structured_tags"), {}),
        "score": normalized,
        "raw_score": round(raw, 3),
        "direct_raw_score": round(direct_raw, 3),
        "matched_fields": dedupe(matched_fields),
        "matched_terms": dedupe(matched_terms),
        "reason": "; ".join(reasons) if reasons else "weak or no direct local metadata match",
        "role": role,
        "keep": normalized > 0,
        "source_paths": source_paths,
    }


def local_search_by_keyword(
    papers: dict[str, dict[str, Any]],
    keywords: list[dict[str, Any]],
    topic: str,
    classification_rules: dict[str, dict[str, list[str]]],
    review_root: Path | None = None,
) -> list[dict[str, Any]]:
    topic_terms = tokenize(topic)
    markdown_cache: dict[str, str] = {}
    grouped: list[dict[str, Any]] = []
    for kw in keywords:
        if not kw.get("keep", True):
            continue
        keyword = kw["keyword"]
        results = [
            score_local_paper(
                meta,
                keyword,
                topic_terms,
                classification_rules,
                markdown_cache,
                review_root,
            )
            for meta in papers.values()
        ]
        results = [
            r for r in results if r["direct_raw_score"] >= 1.0 and r["score"] >= 0.12
        ]
        results.sort(key=lambda r: (r["score"], r["raw_score"], r.get("year") or 0), reverse=True)
        grouped.append(
            {
                "keyword": keyword,
                "category": kw.get("category"),
                "facet_group": kw.get("facet_group"),
                "keep": True,
                "local_results": results,
            }
        )
    return grouped


CROSSREF_USER_AGENT = "chemvellum-discovery/0.4"
EUROPE_PMC_USER_AGENT = "chemvellum-discovery/0.4"


EXTERNAL_RELEVANCE_STOPWORDS = QUERY_STOPWORDS | {
    "all",
    "analysis",
    "approach",
    "approaches",
    "article",
    "battery",
    "batteries",
    "challenge",
    "challenges",
    "design",
    "development",
    "developments",
    "effect",
    "engineering",
    "material",
    "materials",
    "method",
    "methods",
    "new",
    "performance",
    "practical",
    "progress",
    "recent",
    "research",
    "strategy",
    "strategies",
    "system",
    "systems",
}


def _relevance_term(value: str) -> str:
    term = value.lower()
    if len(term) > 4 and term.endswith("ies"):
        return term[:-3] + "y"
    if len(term) > 3 and term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


def external_relevance_terms(value: Any) -> set[str]:
    return {
        _relevance_term(term)
        for term in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(term) >= 3 and term not in EXTERNAL_RELEVANCE_STOPWORDS
    }


def external_relevance(
    keyword: str,
    topic: str,
    title: str,
    abstract: str = "",
    journal: str = "",
) -> dict[str, Any]:
    """Apply one provider-independent topical screen before acquisition."""
    topic_terms = external_relevance_terms(topic)
    query_terms = external_relevance_terms(keyword)
    signal_terms = external_relevance_terms(" ".join([title, abstract, journal]))
    title_terms = external_relevance_terms(title)
    topic_hits = sorted(topic_terms & signal_terms)
    query_hits = sorted(query_terms & signal_terms)
    title_topic_hits = sorted(topic_terms & title_terms)
    topic_floor = (
        1 if 0 < len(topic_terms) <= 2 else min(2, len(topic_terms))
    )
    query_floor = (
        1 if 0 < len(query_terms) <= 2 else min(2, len(query_terms))
    )
    passed = bool(
        len(topic_hits) >= topic_floor
        and len(query_hits) >= query_floor
    )
    topic_ratio = len(topic_hits) / max(len(topic_terms), 1)
    query_ratio = len(query_hits) / max(len(query_terms), 1)
    title_ratio = len(title_topic_hits) / max(len(topic_terms), 1)
    score = min(0.12 + 0.4 * topic_ratio + 0.34 * query_ratio + 0.14 * title_ratio, 1.0)
    return {
        "passed": passed,
        "score": round(score, 4),
        "topic_hits": topic_hits,
        "query_hits": query_hits,
        "topic_floor": topic_floor,
        "query_floor": query_floor,
    }


def is_excluded_crossref_record(item: dict[str, Any]) -> bool:
    record_type = str(item.get("type") or "").lower()
    doi = str(item.get("DOI") or "").lower()
    title = " ".join(item.get("title") or []).lower()
    editorial_title = bool(
        re.search(
            r"^(?:peer[ -]?review|review(?:er)? report|review for|decision letter|"
            r"editor(?:ial)? decision|author response|response to (?:the )?reviewers?)\b",
            title.strip(),
        )
    )
    editorial_doi = bool(
        re.search(
            r"/v\d+/(?:review\d*|decision\d*|response\d*|peer-review\d*)$",
            doi,
        )
    )
    return (
        record_type in {"component", "peer-review"}
        or bool(re.search(r"\.s\d+$", doi))
        or "mtgabs" in doi
        or record_type == "proceedings-article"
        or "supporting information" in title
        or "supplementary material" in title
        or editorial_title
        or editorial_doi
    )


def is_supplementary_crossref_record(item: dict[str, Any]) -> bool:
    """Backward-compatible name for the broader non-article filter."""
    return is_excluded_crossref_record(item)


def crossref_request_json(url: str) -> dict[str, Any]:
    retryable = (
        http.client.IncompleteRead,
        json.JSONDecodeError,
        ssl.SSLError,
        TimeoutError,
        urllib.error.URLError,
    )
    for attempt in range(2):
        req = urllib.request.Request(url, headers={"User-Agent": CROSSREF_USER_AGENT})
        try:
            with urllib.request.urlopen(
                req,
                context=ssl.create_default_context(),
                timeout=20,
            ) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except retryable:
            if attempt:
                raise
            time.sleep(0.5)
    raise RuntimeError("unreachable Crossref request state")


def crossref_search_items(
    query: str,
    rows: int,
    query_field: str = "query.bibliographic",
) -> list[dict[str, Any]]:
    params = {
        query_field: query,
        "filter": "type:journal-article",
        "rows": str(min(max(rows, 1), 100)),
    }
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    return list(crossref_request_json(url).get("message", {}).get("items", []))


def fetch_crossref_work(doi: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(_normalized_doi(doi), safe="")
    return dict(crossref_request_json(f"https://api.crossref.org/works/{encoded}").get("message") or {})


def fetch_crossref_works(dois: list[str], batch_size: int = 20) -> list[dict[str, Any]]:
    """Hydrate arbitrary DOI sets with Crossref's repeatable DOI filter."""
    normalized = dedupe([_normalized_doi(doi) for doi in dois if _normalized_doi(doi)])
    works: list[dict[str, Any]] = []
    for start in range(0, len(normalized), max(batch_size, 1)):
        batch = normalized[start : start + max(batch_size, 1)]
        filters = ["type:journal-article", *(f"doi:{doi}" for doi in batch)]
        params = {"filter": ",".join(filters), "rows": str(len(batch))}
        url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
        works.extend(crossref_request_json(url).get("message", {}).get("items", []))
    return works


def _crossref_query(keyword: str, topic: str) -> str:
    # Each planned keyword is already a complete query for one contract
    # dimension. Appending the whole title over-constrains provider search.
    return re.sub(r"\s+", " ", keyword).strip() or re.sub(r"\s+", " ", topic).strip()


def crossref_item_to_result(
    item: dict[str, Any],
    keyword: str,
    topic: str,
    *,
    source: str = "crossref",
    reason: str = "Crossref title/snippet/topic/DOI overlap score",
) -> dict[str, Any]:
    title = " ".join(item.get("title") or []) or "(untitled)"
    container = " ".join(item.get("container-title") or [])
    abstract = re.sub("<[^>]+>", " ", item.get("abstract") or "")
    hay = " ".join([title, container, abstract]).lower()
    topic_terms = tokenize(topic)
    score = 0.0
    if keyword.lower() in hay:
        score += 0.55
    score += min(sum(1 for term in topic_terms if term in hay) * 0.04, 0.32)
    if item.get("DOI"):
        score += 0.08
    year = None
    for date_key in ("issued", "published", "published-print", "published-online"):
        date_parts = item.get(date_key, {}).get("date-parts") or []
        if date_parts and date_parts[0]:
            year = date_parts[0][0]
            break
    if isinstance(year, int) and year >= 2020:
        score += 0.05
    doi = item.get("DOI")
    link = f"https://doi.org/{doi}" if doi else item.get("URL", "")
    pdf_links = [
        str(entry.get("URL") or "").strip()
        for entry in item.get("link") or []
        if isinstance(entry, dict)
        and "pdf" in str(entry.get("content-type") or "").lower()
        and str(entry.get("URL") or "").strip()
    ]
    license_urls = [
        str(entry.get("URL") or "").strip()
        for entry in item.get("license") or []
        if isinstance(entry, dict) and str(entry.get("URL") or "").strip()
    ]
    has_open_license = any(
        "creativecommons.org" in license_url.lower() for license_url in license_urls
    )
    resource = item.get("resource") or {}
    primary_resource = resource.get("primary") if isinstance(resource, dict) else {}
    primary_url = (
        str(primary_resource.get("URL") or "").strip()
        if isinstance(primary_resource, dict)
        else ""
    )
    open_pdf_url = pdf_links[0] if pdf_links and has_open_license else ""
    open_full_text_url = (open_pdf_url or primary_url) if has_open_license else ""
    relevance = external_relevance(keyword, topic, title, abstract, container)
    return {
        "external_id": doi or str(item.get("URL") or ""),
        "title": title,
        "authors": format_crossref_authors(item.get("author", [])),
        "year": year,
        "journal": container,
        "doi": doi,
        "url": link,
        "abstract": re.sub(r"\s+", " ", abstract).strip()[:1200],
        "citation_count": item.get("is-referenced-by-count"),
        "record_type": item.get("type"),
        "publication_types": [item.get("type")] if item.get("type") else [],
        "open_access_pdf_url": open_pdf_url,
        "open_access_full_text_url": open_full_text_url,
        "publisher_pdf_url_candidate": pdf_links[0] if pdf_links else "",
        "license_urls": license_urls,
        "score": round(min(max(score, relevance["score"]), 1.0), 4),
        "reason": reason,
        "relevance": relevance,
        "keep": relevance["passed"],
        "source": source,
    }


def web_search(keyword: str, topic: str, limit: int = 8) -> list[dict[str, Any]]:
    query = _crossref_query(keyword, topic)
    rows = min(max(limit * 4, 20), 100)
    try:
        items = crossref_search_items(query, rows)
    except Exception as exc:
        return [{"title": f"WEB_SEARCH_FAILED: {type(exc).__name__}", "url": "", "score": 0, "reason": str(exc), "keep": False}]
    results: list[dict[str, Any]] = []
    for item in items:
        if is_excluded_crossref_record(item):
            continue
        results.append(crossref_item_to_result(item, keyword, topic))
    results.sort(key=lambda r: (r["score"], r.get("year") or 0), reverse=True)
    return results[:limit]


def europe_pmc_search(
    keyword: str,
    topic: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Search Europe PMC for immediately ingestible open full text.

    Europe PMC is queried as a discovery provider, not only as a DOI fallback.
    Broad provider recall is followed by the same topic/query screen used for
    Crossref, so an unrelated open article cannot enter merely because it is
    easy to download.
    """
    normalized_query = re.sub(r"\s+", " ", keyword or topic).strip()
    query = f"({normalized_query}) AND OPEN_ACCESS:Y"
    params = {
        "query": query,
        "format": "json",
        "resultType": "core",
        "pageSize": str(min(max(limit * 4, 20), 100)),
    }
    url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
        + urllib.parse.urlencode(params)
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": EUROPE_PMC_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(
            request,
            context=ssl.create_default_context(),
            timeout=25,
        ) as response:
            payload = json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
    except Exception as exc:
        return [
            {
                "title": f"EUROPE_PMC_SEARCH_FAILED: {type(exc).__name__}",
                "url": "",
                "score": 0,
                "reason": str(exc),
                "keep": False,
                "source": "europe_pmc",
            }
        ]

    results: list[dict[str, Any]] = []
    for item in (payload.get("resultList") or {}).get("result") or []:
        pmcid = str(item.get("pmcid") or "").strip().upper()
        if (
            not pmcid
            or str(item.get("isOpenAccess") or "").upper() != "Y"
            or str(item.get("inEPMC") or "").upper() != "Y"
        ):
            continue
        title = re.sub(r"\s+", " ", str(item.get("title") or "(untitled)")).strip()
        abstract = re.sub(r"\s+", " ", str(item.get("abstractText") or "")).strip()
        journal = str(item.get("journalTitle") or "").strip()
        relevance = external_relevance(keyword, topic, title, abstract, journal)
        doi = _normalized_doi(item.get("doi"))
        author_rows = (item.get("authorList") or {}).get("author") or []
        authors = [
            str(author.get("fullName") or "").strip()
            for author in author_rows
            if isinstance(author, dict) and str(author.get("fullName") or "").strip()
        ]
        publication_types = (item.get("pubTypeList") or {}).get("pubType") or []
        article_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
        repository_url = (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/"
            f"{pmcid}/fullTextXML"
        )
        results.append(
            {
                "external_id": doi or pmcid,
                "title": title,
                "authors": authors,
                "year": int(item["pubYear"]) if str(item.get("pubYear") or "").isdigit() else None,
                "journal": journal,
                "doi": doi,
                "url": f"https://doi.org/{doi}" if doi else article_url,
                "abstract": abstract[:1200],
                "citation_count": item.get("citedByCount"),
                "publication_types": publication_types,
                "open_access_pdf_url": "",
                "open_access_full_text_url": article_url,
                "repository_provider": "europe_pmc",
                "repository_id": pmcid,
                "repository_format": "jats_xml",
                "repository_full_text_url": repository_url,
                "license": item.get("license"),
                "score": round(min(relevance["score"] + 0.08, 1.0), 4),
                "reason": "Europe PMC relevance rank, topic screen, and ingestible OA full text",
                "relevance": relevance,
                "keep": relevance["passed"],
                "source": "europe_pmc",
            }
        )
    results.sort(
        key=lambda row: (
            bool(row.get("keep")),
            row.get("score") or 0,
            row.get("citation_count") or 0,
        ),
        reverse=True,
    )
    return results[:limit]


def format_crossref_authors(authors: list[dict[str, Any]]) -> list[str]:
    out = []
    for author in authors[:8]:
        name = " ".join(x for x in [author.get("given"), author.get("family")] if x)
        if name:
            out.append(name)
    return out


REVIEW_SEED_RE = re.compile(
    r"\b(review|perspective|guidelines?|state of the art|recent advances|research progress)\b",
    re.I,
)

REFERENCE_SCREEN_STOPWORDS = {
    "about", "after", "against", "and", "based", "between", "chemical", "chemistry",
    "control", "effect", "effects", "formation", "from", "materials", "paper", "process",
    "for", "reaction", "review", "state", "studies", "study", "the", "their",
    "through", "using", "with",
}


def review_seed_hints_from_grouped(
    grouped_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reuse relevant reviews already found by any provider as Crossref seeds."""
    hints: list[dict[str, Any]] = []
    seen_dois: set[str] = set()
    for group in grouped_results:
        for row in group.get("web_results") or []:
            if not isinstance(row, dict) or not row.get("keep", True):
                continue
            doi = _normalized_doi(row.get("doi") or row.get("external_id"))
            title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
            publication_types = [
                str(value)
                for value in row.get("publication_types") or []
            ]
            review_like = bool(REVIEW_SEED_RE.search(title)) or any(
                "review" in value.lower() for value in publication_types
            )
            if not doi or doi in seen_dois or not review_like:
                continue
            seen_dois.add(doi)
            hints.append(
                {
                    "DOI": doi,
                    "title": [title],
                    "type": "journal-article",
                    "_review_hint": True,
                }
            )
    return hints


def _reference_screen_terms(topic: str, keywords: list[str]) -> list[str]:
    return [
        term
        for term in tokenize(" ".join([topic] + keywords[:4]))
        if term not in REFERENCE_SCREEN_STOPWORDS
    ]


def _reference_relevance(title: str, topic: str, keywords: list[str]) -> tuple[float, list[str]]:
    signal = title.lower()
    terms = _reference_screen_terms(topic, keywords)
    hits = dedupe([term for term in terms if contains_term(signal, term)])
    phrase_hit = any(
        len(tokenize(keyword)) >= 2 and keyword.lower() in signal
        for keyword in keywords
    )
    score = min(0.12 + len(hits) * 0.09 + (0.28 if phrase_hit else 0.0), 0.95)
    return score, hits


def _reference_stub(reference: dict[str, Any], seed_doi: str) -> dict[str, Any] | None:
    doi = str(reference.get("DOI") or "").strip()
    if not doi:
        return None
    title = re.sub(r"\s+", " ", str(reference.get("article-title") or "")).strip()
    return {
        "doi": doi,
        "title": title,
        "journal": str(reference.get("journal-title") or "").strip(),
        "year": reference.get("year"),
        "authors": [str(reference.get("author") or "").strip()] if reference.get("author") else [],
        "cited_by_review_dois": [seed_doi],
    }


def crossref_reference_expansion(
    topic: str,
    keywords: list[str],
    *,
    seed_limit: int = 2,
    result_limit: int = 30,
    seed_hints: list[dict[str, Any]] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Find close reviews, expand deposited references, and screen them by topic."""
    if result_limit <= 0:
        return {"status": "disabled", "seed_query": "", "seeds": [], "results": [], "errors": []}
    seed_basis = keywords[0] if keywords else topic
    concise_terms = _reference_screen_terms(topic, keywords)[:4]
    anchor_terms = concise_terms[:2]
    acronyms = dedupe(re.findall(r"\b[A-Z][A-Z0-9]{1,7}\b", " ".join(keywords)))
    method_terms = [
        term
        for term in _reference_screen_terms(topic, keywords)
        if term in {"enzyme", "enzymatic", "hydrolase"} or term.endswith("ase")
    ]
    seed_queries = dedupe(
        [
            f"{seed_basis} review".strip(),
            f"{' '.join(concise_terms)} review".strip(),
            f"{' '.join(anchor_terms)} review".strip(),
            *(
                f"{acronym} {method_term} guidelines"
                for acronym in acronyms[:2]
                for method_term in method_terms[:3]
            ),
        ]
    )
    seed_query = seed_queries[0]
    errors: list[str] = []
    raw_seed_items: list[dict[str, Any]] = []
    seen_seed_dois: set[str] = set()
    for item in seed_hints or []:
        doi = _normalized_doi(item.get("DOI"))
        if doi and doi not in seen_seed_dois:
            seen_seed_dois.add(doi)
            raw_seed_items.append(item)
    for index, query in enumerate(seed_queries):
        if progress_callback:
            progress_callback(
                f"reference expansion seed search {index + 1}/{len(seed_queries)}: {query}"
            )
        try:
            items = crossref_search_items(
                query,
                30,
                query_field="query.title" if index == 0 else "query.bibliographic",
            )
        except Exception as exc:
            errors.append(f"seed search {query}: {exc}")
            if progress_callback:
                progress_callback(
                    f"reference expansion seed search {index + 1}/{len(seed_queries)} failed: {type(exc).__name__}"
                )
            continue
        if progress_callback:
            progress_callback(
                f"reference expansion seed search {index + 1}/{len(seed_queries)} returned {len(items)} records"
            )
        for item in items:
            doi = _normalized_doi(item.get("DOI"))
            if doi and doi not in seen_seed_dois:
                seen_seed_dois.add(doi)
                raw_seed_items.append(item)
    if not raw_seed_items and errors:
        return {
            "status": "error",
            "seed_query": seed_query,
            "seed_queries": seed_queries,
            "seeds": [],
            "results": [],
            "errors": dedupe(errors),
        }

    seed_candidates: list[dict[str, Any]] = []
    for item in raw_seed_items:
        title = " ".join(item.get("title") or [])
        doi = str(item.get("DOI") or "").strip()
        relevance, hits = _reference_relevance(title, topic, keywords)
        anchor_match = all(contains_term(title, term) for term in anchor_terms)
        guideline_match = "guideline" in title.lower() and len(hits) >= 2
        partial_anchor = any(contains_term(title, term) for term in anchor_terms)
        review_like = bool(REVIEW_SEED_RE.search(title)) or bool(
            item.get("_review_hint")
        )
        if (
            not doi
            or is_excluded_crossref_record(item)
            or not review_like
            or not (anchor_match or guideline_match or partial_anchor)
            or (
                len(hits) < min(2, len(_reference_screen_terms(topic, keywords)))
                and not guideline_match
                and not partial_anchor
            )
        ):
            continue
        seed_candidates.append(
            {
                "item": item,
                "relevance": relevance,
                "hits": hits,
                "anchor_match": anchor_match,
                "guideline_match": guideline_match,
            }
        )
    seed_candidates.sort(key=lambda row: row["relevance"], reverse=True)

    detailed_candidates: list[dict[str, Any]] = []
    detail_pool = seed_candidates[:8]
    for candidate in seed_candidates:
        if candidate["guideline_match"] and candidate not in detail_pool:
            detail_pool.append(candidate)
    for detail_index, candidate in enumerate(detail_pool, start=1):
        doi = str(candidate["item"].get("DOI") or "")
        if progress_callback:
            progress_callback(
                f"reference expansion seed detail {detail_index}/{len(detail_pool)}: {doi}"
            )
        try:
            work = fetch_crossref_work(doi)
        except Exception as exc:
            errors.append(f"seed {doi}: {exc}")
            continue
        references = list(work.get("reference") or [])
        doi_references = sum(bool(ref.get("DOI")) for ref in references if isinstance(ref, dict))
        titled_references = sum(bool(ref.get("article-title")) for ref in references if isinstance(ref, dict))
        candidate_quality = (
            float(candidate["relevance"])
            + min(doi_references / 200.0, 0.35)
            + min(titled_references / max(doi_references, 1), 1.0) * 0.25
        )
        detailed_candidates.append(
            {
                **candidate,
                "work": work,
                "doi_reference_count": doi_references,
                "titled_reference_count": titled_references,
                "candidate_quality": round(candidate_quality, 4),
            }
        )
    detailed_candidates.sort(key=lambda row: row["candidate_quality"], reverse=True)
    best_quality = detailed_candidates[0]["candidate_quality"] if detailed_candidates else 0.0
    selected_seeds = [
        row
        for row in detailed_candidates
        if row["candidate_quality"] >= best_quality - 0.12
    ][: max(seed_limit, 0)]
    if len(selected_seeds) < max(seed_limit, 0):
        for row in detailed_candidates:
            if row.get("guideline_match") and row not in selected_seeds:
                selected_seeds.append(row)
                break

    seed_reports: list[dict[str, Any]] = []
    seed_results: list[dict[str, Any]] = []
    reference_stubs: dict[str, dict[str, Any]] = {}
    for selected in selected_seeds:
        work = selected["work"]
        seed_doi = str(work.get("DOI") or selected["item"].get("DOI") or "")
        seed_title = " ".join(work.get("title") or selected["item"].get("title") or [])
        seed_reports.append(
            {
                "doi": seed_doi,
                "title": seed_title,
                "topic_term_hits": selected["hits"],
                "deposited_reference_count": len(work.get("reference") or []),
                "references_with_doi_count": selected["doi_reference_count"],
                "references_with_title_count": selected["titled_reference_count"],
            }
        )
        seed_row = crossref_item_to_result(
            work,
            seed_basis,
            topic,
            source="crossref_review_seed",
            reason="Close review selected as a reference-expansion seed",
        )
        seed_row["keep"] = True
        seed_row["reference_expansion_role"] = "seed_review"
        seed_results.append(seed_row)
        for reference in work.get("reference") or []:
            if not isinstance(reference, dict):
                continue
            stub = _reference_stub(reference, seed_doi)
            if stub is None:
                continue
            key = _normalized_doi(stub["doi"])
            if key in reference_stubs:
                reference_stubs[key]["cited_by_review_dois"] = dedupe(
                    reference_stubs[key]["cited_by_review_dois"] + [seed_doi]
                )
            else:
                reference_stubs[key] = stub

    # Hydrate the bounded reference pool in batches. This gives DOI-only
    # deposits the same title/OA screen and supplies citation counts for a
    # small high-impact lane alongside the top lexical matches.
    if progress_callback:
        progress_callback(
            f"reference expansion hydrating {min(len(reference_stubs), 300)} deposited references"
        )
    try:
        hydrated_references = fetch_crossref_works(list(reference_stubs)[:300])
    except Exception as exc:
        errors.append(f"reference batch hydration: {exc}")
        hydrated_references = []
    if progress_callback:
        progress_callback(
            f"reference expansion hydrated {len(hydrated_references)} references"
        )
    hydrated_by_doi = {
        _normalized_doi(work.get("DOI")): work
        for work in hydrated_references
        if not is_excluded_crossref_record(work)
    }

    screened: list[dict[str, Any]] = []
    for doi, stub in reference_stubs.items():
        work = hydrated_by_doi.get(doi)
        title = " ".join(work.get("title") or []) if work else stub["title"]
        if not title:
            continue
        relevance, hits = _reference_relevance(title, topic, keywords)
        if len(hits) < 2:
            continue
        citation_count = work.get("is-referenced-by-count") if work else None
        try:
            citation_count = int(citation_count or 0)
        except (TypeError, ValueError):
            citation_count = 0
        screened.append(
            {
                "doi": doi,
                "title": title,
                "work": work,
                "reference_relevance": relevance,
                "topic_term_hits": hits,
                "cited_by_review_dois": stub["cited_by_review_dois"],
                "citation_count": citation_count,
            }
        )
    screened.sort(
        key=lambda row: (row["reference_relevance"], row["citation_count"]),
        reverse=True,
    )
    impact_slots = min(result_limit // 3, 10)
    topic_slots = max(result_limit - impact_slots, 0)
    shortlist = screened[:topic_slots]
    shortlisted_dois = {_normalized_doi(row["doi"]) for row in shortlist}
    for candidate in sorted(
        screened,
        key=lambda row: (row["citation_count"], row["reference_relevance"]),
        reverse=True,
    ):
        doi = _normalized_doi(candidate["doi"])
        if doi in shortlisted_dois:
            continue
        shortlist.append(candidate)
        shortlisted_dois.add(doi)
        if len(shortlist) >= result_limit:
            break

    expanded_results: list[dict[str, Any]] = []
    for candidate in shortlist:
        work = candidate.get("work")
        if not isinstance(work, dict):
            work = {
                "DOI": candidate["doi"],
                "title": [candidate["title"]],
                "container-title": [candidate.get("journal") or ""],
                "issued": {"date-parts": [[candidate.get("year")]]} if candidate.get("year") else {},
            }
        if is_excluded_crossref_record(work):
            continue
        row = crossref_item_to_result(
            work,
            seed_basis,
            topic,
            source="crossref_reference_expansion",
            reason="Topic-screened reference from a close review",
        )
        row["score"] = max(float(row.get("score") or 0), candidate["reference_relevance"])
        row["keep"] = True
        row["reference_expansion_role"] = "cited_candidate"
        row["cited_by_review_dois"] = candidate["cited_by_review_dois"]
        row["topic_term_hits"] = candidate["topic_term_hits"]
        expanded_results.append(row)

    results = merge_external_results(seed_results + expanded_results)
    if selected_seeds and expanded_results:
        status = "ok"
    elif selected_seeds:
        status = "seeds_found_no_retained_references"
    else:
        status = "no_close_review_seed"
    return {
        "status": status,
        "seed_query": seed_query,
        "seed_queries": seed_queries,
        "seeds": seed_reports,
        "unique_deposited_reference_dois": len(reference_stubs),
        "screened_reference_count": len(screened),
        "retained_reference_count": len(expanded_results),
        "results": results,
        "errors": dedupe(errors),
    }


def semantic_scholar_search(
    keyword: str,
    topic: str,
    limit: int = 8,
    api_key: str = "",
) -> list[dict[str, Any]]:
    """Retrieve relevance-ranked metadata plus legal OA PDF locations.

    The public Academic Graph endpoint works without a key, although the
    unauthenticated pool is shared and may be throttled.  Search is deliberately
    metadata-only; downloading and MinerU parsing are a separate explicit action.
    """
    query = re.sub(r"[-–—]+", " ", keyword or topic)
    fields = ",".join(
        [
            "paperId",
            "title",
            "authors",
            "year",
            "venue",
            "abstract",
            "url",
            "externalIds",
            "openAccessPdf",
            "citationCount",
            "publicationTypes",
        ]
    )
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(
        {"query": query, "limit": str(max(1, min(limit, 100))), "fields": fields}
    )
    headers = {"User-Agent": "chemvellum-discovery/0.2"}
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return [
            {
                "title": f"SEMANTIC_SCHOLAR_SEARCH_FAILED: {type(exc).__name__}",
                "url": "",
                "score": 0,
                "reason": str(exc),
                "keep": False,
                "source": "semantic_scholar",
            }
        ]

    topic_terms = tokenize(topic)
    results: list[dict[str, Any]] = []
    for item in data.get("data") or []:
        title = re.sub(r"\s+", " ", str(item.get("title") or "(untitled)")).strip()
        abstract = re.sub(r"\s+", " ", str(item.get("abstract") or "")).strip()
        venue = str(item.get("venue") or "").strip()
        haystack = " ".join([title, abstract, venue]).lower()
        relevance = external_relevance(keyword, topic, title, abstract, venue)
        score = 0.18  # The API response is already relevance-ranked.
        if keyword.lower() in haystack:
            score += 0.45
        score += min(sum(1 for term in topic_terms if term in haystack) * 0.035, 0.25)
        external_ids = item.get("externalIds") or {}
        doi = str(external_ids.get("DOI") or "").strip()
        if doi:
            score += 0.05
        open_pdf = item.get("openAccessPdf") or {}
        open_pdf_url = str(open_pdf.get("url") or "").strip() if isinstance(open_pdf, dict) else ""
        if open_pdf_url:
            score += 0.07
        year = item.get("year")
        if isinstance(year, int) and year >= 2020:
            score += 0.03
        authors = [
            str(author.get("name") or "").strip()
            for author in item.get("authors") or []
            if isinstance(author, dict) and str(author.get("name") or "").strip()
        ]
        results.append(
            {
                "external_id": str(item.get("paperId") or ""),
                "title": title,
                "authors": authors,
                "year": year,
                "journal": venue,
                "doi": doi,
                "url": str(item.get("url") or (f"https://doi.org/{doi}" if doi else "")),
                "abstract": abstract[:1200],
                "citation_count": item.get("citationCount"),
                "publication_types": item.get("publicationTypes") or [],
                "open_access_pdf_url": open_pdf_url,
                "open_access_status": open_pdf.get("status") if isinstance(open_pdf, dict) else None,
                "score": round(min(max(score, relevance["score"]), 1.0), 4),
                "reason": "Semantic Scholar relevance rank plus topic/DOI/OA metadata",
                "relevance": relevance,
                "keep": relevance["passed"],
                "source": "semantic_scholar",
            }
        )
    results.sort(key=lambda row: (row.get("score") or 0, row.get("year") or 0), reverse=True)
    return results


def _normalized_doi(value: Any) -> str:
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", str(value or "").strip(), flags=re.I).lower()


def _normalized_title(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def annotate_external_results(
    rows: list[dict[str, Any]],
    local_papers: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    doi_to_local: dict[str, str] = {}
    title_to_local: dict[str, str] = {}
    for paper_id, meta in local_papers.items():
        doi = _normalized_doi(field_value(meta.get("doi")))
        title = _normalized_title(field_value(meta.get("title")))
        if doi:
            doi_to_local[doi] = paper_id
        if title:
            title_to_local[title] = paper_id
    annotated: list[dict[str, Any]] = []
    for row in rows:
        doi = _normalized_doi(row.get("doi"))
        title = _normalized_title(row.get("title"))
        local_paper_id = doi_to_local.get(doi) if doi else None
        if not local_paper_id and title:
            local_paper_id = title_to_local.get(title)
        pdf_url = str(row.get("open_access_pdf_url") or "").strip()
        full_text_url = str(row.get("open_access_full_text_url") or "").strip()
        if local_paper_id:
            promotion_status = "already_local"
        elif pdf_url:
            promotion_status = "ready_to_download"
        elif full_text_url:
            promotion_status = "full_text_located_needs_pdf"
        else:
            promotion_status = "needs_pdf_location"
        annotated.append(
            {
                **row,
                "local_paper_id": local_paper_id,
                "promotion_status": promotion_status,
                "evidence_status": "full_text_available" if local_paper_id else "coverage_only",
            }
        )
    return annotated


def valid_unpaywall_email(value: str) -> bool:
    email = str(value or "").strip()
    return bool(
        re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
        and email.lower() != "research@university.edu"
    )


def unpaywall_location(doi: str, email: str) -> dict[str, Any]:
    params = urllib.parse.urlencode({"email": email})
    encoded_doi = urllib.parse.quote(_normalized_doi(doi), safe="")
    request = urllib.request.Request(
        f"https://api.unpaywall.org/v2/{encoded_doi}?{params}",
        headers={"User-Agent": "chemvellum-discovery/0.4"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    if not payload.get("is_oa"):
        return {}
    locations = [
        payload.get("best_oa_location") or {},
        *(payload.get("oa_locations") or []),
    ]
    for location in locations:
        if not isinstance(location, dict):
            continue
        pdf_url = str(location.get("url_for_pdf") or "").strip()
        landing_url = str(location.get("url_for_landing_page") or "").strip()
        if not (pdf_url or landing_url):
            continue
        return {
            "open_access_pdf_url": pdf_url,
            "open_access_full_text_url": pdf_url or landing_url,
            "license": location.get("license"),
            "oa_resolver": "unpaywall",
            "oa_host_type": location.get("host_type"),
        }
    return {}


def enrich_grouped_open_access(
    grouped: list[dict[str, Any]],
    email: str,
    limit: int,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if not email:
        return {
            "status": "disabled_missing_email",
            "attempted": 0,
            "resolved": 0,
            "errors": [],
        }
    if not valid_unpaywall_email(email):
        return {
            "status": "disabled_invalid_email",
            "attempted": 0,
            "resolved": 0,
            "errors": ["UNPAYWALL_EMAIL must be a real contact address, not a placeholder."],
        }
    cache: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    attempted = 0
    resolved_dois: set[str] = set()
    for group in grouped:
        for row in group.get("web_results") or []:
            if row.get("open_access_pdf_url") or row.get("repository_full_text_url"):
                continue
            doi = _normalized_doi(row.get("doi"))
            if not doi:
                continue
            if doi not in cache:
                if limit > 0 and attempted >= limit:
                    continue
                attempted += 1
                if progress_callback and (attempted == 1 or attempted % 5 == 0):
                    limit_label = str(limit) if limit > 0 else "unbounded"
                    progress_callback(
                        f"open-access resolution {attempted}/{limit_label}: {doi}"
                    )
                try:
                    cache[doi] = unpaywall_location(doi, email)
                except Exception as exc:
                    cache[doi] = {}
                    errors.append(f"{doi}: {type(exc).__name__}: {exc}")
            location = cache[doi]
            if not location:
                continue
            was_located = bool(
                row.get("open_access_pdf_url")
                or row.get("open_access_full_text_url")
            )
            row.update(location)
            row["promotion_status"] = (
                "ready_to_download"
                if row.get("open_access_pdf_url")
                else "full_text_located_needs_pdf"
            )
            if not was_located:
                resolved_dois.add(doi)
    return {
        "status": "ok" if not errors else "partial_error",
        "attempted": attempted,
        "resolved": len(resolved_dois),
        "errors": dedupe(errors),
    }


def choose_external_groups(
    local_grouped: list[dict[str, Any]],
    query_limit: int,
) -> list[dict[str, Any]]:
    if query_limit <= 0:
        return local_grouped
    # Visit each declared facet once before spending remaining calls on the
    # second chunk of a dense facet. This keeps a technical call cap from
    # erasing later coverage dimensions.
    buckets: dict[str, list[dict[str, Any]]] = {}
    for index, group in enumerate(local_grouped):
        key = str(group.get("facet_group") or f"query_{index}")
        buckets.setdefault(key, []).append(group)
    selected: list[dict[str, Any]] = []
    depth = 0
    while len(selected) < query_limit:
        added = False
        for rows in buckets.values():
            if depth < len(rows):
                selected.append(rows[depth])
                added = True
                if len(selected) >= query_limit:
                    break
        if not added:
            break
        depth += 1
    return selected




def normalize_sciatlas_paper(item: dict[str, Any]) -> dict[str, Any]:
    # SciAtlas /v1/search nests the canonical record in `paper`; fall back to top-level keys.
    nested = item.get("paper") if isinstance(item.get("paper"), dict) else {}
    def first(*keys):
        for src in (item, nested):
            for k in keys:
                v = src.get(k)
                if v not in (None, "", []):
                    return v
        return None
    title = first("title", "paper_title") or "(untitled)"
    if isinstance(title, str):
        title = title.replace("\n", " ").strip()
    authors = first("authors", "author_names") or []
    if isinstance(authors, list):
        normalized_authors: list[str] = []
        for entry in authors:
            if isinstance(entry, str):
                normalized_authors.append(entry)
            elif isinstance(entry, dict):
                name = entry.get("name") or entry.get("display_name")
                if not name:
                    parts = [entry.get("given"), entry.get("family")]
                    name = " ".join(x for x in parts if x).strip()
                if name:
                    normalized_authors.append(name)
        authors = normalized_authors
    else:
        authors = []
    year = first("year", "publication_year")
    journal = first("journal", "venue", "container_title", "venue_source_display_name") or ""
    doi = first("doi", "DOI") or ""
    if isinstance(doi, str) and doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]
    paper_url = first("paper_url", "pdf_url", "url", "html_url")
    url = paper_url or (f"https://doi.org/{doi}" if doi else "")
    abstract = first("abstract") or ""
    raw_score = item.get("score") or item.get("relevance_score") or item.get("graph_score") or 0.0
    try:
        raw_score = float(raw_score)
    except (TypeError, ValueError):
        raw_score = 0.0
    # SciAtlas scores can exceed 1; clamp + soft normalize for UI consistency.
    norm = min(round(raw_score / 10.0, 4) if raw_score > 1 else round(raw_score, 4), 1.0)
    return {
        "title": title,
        "authors": authors,
        "year": year,
        "journal": journal,
        "doi": doi,
        "url": url,
        "abstract": abstract[:600],
        "score": norm,
        "raw_score": raw_score,
        "reason": "SciAtlas KG retrieval (hybrid)",
        "keep": norm > 0,
        "source": "sciatlas",
    }


def sciatlas_search(
    client: SciAtlasClient,
    keyword: str,
    topic: str,
    limit: int,
    time_range: str | None,
    domain: str | None,
) -> list[dict[str, Any]]:
    try:
        response = client.search_papers(
            query=topic or keyword,
            keyword=keyword,
            top_k=max(limit, 1),
            retrieval_mode="hybrid",
            time_range=time_range,
            domain=domain,
        )
    except Exception as exc:
        return [{"title": f"SCIATLAS_SEARCH_FAILED: {type(exc).__name__}", "url": "", "score": 0, "reason": str(exc), "keep": False, "source": "sciatlas"}]
    results = [normalize_sciatlas_paper(item) for item in papers_from_response(response)]
    results.sort(key=lambda r: (r.get("score", 0), r.get("year") or 0), reverse=True)
    return results



def _result_dedupe_key(row: dict[str, Any]) -> str:
    doi = (row.get("doi") or "").strip().lower()
    if doi:
        return "doi:" + doi
    url = (row.get("url") or "").strip().lower()
    if url:
        return "url:" + url
    title = re.sub(r"\s+", " ", str(row.get("title") or "").strip().lower())
    return "title:" + title


def merge_external_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scalar_fields = (
        "doi",
        "url",
        "abstract",
        "journal",
        "year",
        "open_access_pdf_url",
        "open_access_full_text_url",
        "repository_provider",
        "repository_id",
        "repository_format",
        "repository_full_text_url",
        "license",
    )
    list_fields = ("authors", "publication_types", "license_urls")
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _result_dedupe_key(row)
        if not key:
            continue
        if key not in merged:
            merged[key] = {**row, "sources": [row.get("source", "external")]}
            order.append(key)
            continue
        existing = merged[key]
        src = row.get("source", "external")
        if src not in existing.get("sources", []):
            existing.setdefault("sources", []).append(src)
        if (row.get("score") or 0) > (existing.get("score") or 0):
            # Promote the higher-scoring record without discarding an OA
            # location contributed by a different metadata provider.
            sources = existing.get("sources", [])
            promoted = {**row, "sources": sources}
            for field in scalar_fields:
                if not promoted.get(field) and existing.get(field):
                    promoted[field] = existing[field]
            for field in list_fields:
                promoted[field] = dedupe(
                    [
                        *list(promoted.get(field) or []),
                        *list(existing.get(field) or []),
                    ]
                )
            merged[key] = promoted
            existing = merged[key]
        for field in scalar_fields:
            if not existing.get(field) and row.get(field):
                existing[field] = row[field]
        for field in list_fields:
            existing[field] = dedupe(
                [
                    *list(existing.get(field) or []),
                    *list(row.get(field) or []),
                ]
            )
    out: list[dict[str, Any]] = []
    for key in order:
        row = merged[key]
        sources = row.get("sources") or [row.get("source", "external")]
        # Keep `source` as the primary (highest-scoring) one for backward compat.
        row["source"] = sources[0] if len(sources) == 1 else "+".join(sources)
        row["sources"] = sources
        out.append(row)
    out.sort(key=lambda r: (r.get("score") or 0, r.get("year") or 0), reverse=True)
    return out


def split_provider_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in rows:
        title = str(row.get("title") or "") if isinstance(row, dict) else ""
        if isinstance(row, dict) and "_SEARCH_FAILED:" in title:
            errors.append(str(row.get("reason") or title))
        elif isinstance(row, dict):
            records.append(row)
    return records, errors


def provider_run_status(
    requested: bool,
    stats: dict[str, int],
    errors: list[str],
    unavailable_status: str = "",
) -> str:
    if not requested:
        return "disabled"
    if unavailable_status and stats.get("attempted_queries", 0) == 0:
        return unavailable_status
    if stats.get("successful_queries", 0) == 0 and errors:
        return "error"
    if errors:
        return "partial_error"
    if stats.get("returned_records", 0) == 0:
        return "no_results"
    if stats.get("retained_records", 0) == 0:
        return "no_retained_results"
    return "ok"

def combine_results(local_grouped: list[dict[str, Any]], web_grouped: list[dict[str, Any]]) -> list[dict[str, Any]]:
    web_map = {g["keyword"]: g for g in web_grouped}
    combined = []
    for group in local_grouped:
        keyword = group["keyword"]
        combined.append(
            {
                "keyword": keyword,
                "category": group.get("category"),
                "keep": group.get("keep", True),
                "local_results": group.get("local_results", []),
                "web_results": web_map.get(keyword, {}).get("web_results", []),
            }
        )
    local_keywords = {group["keyword"] for group in local_grouped}
    for group in web_grouped:
        if group["keyword"] in local_keywords:
            continue
        combined.append(
            {
                "keyword": group["keyword"],
                "category": group.get("category", "document_scope"),
                "keep": group.get("keep", True),
                "local_results": [],
                "web_results": group.get("web_results", []),
            }
        )
    return combined


RANKING_STOPWORDS = {
    "about", "across", "after", "available", "between", "central", "directly",
    "effects", "evidence", "important", "involving", "methods", "paper", "papers",
    "present", "question", "reaction", "relevant", "scope", "studied", "studies",
    "their", "through", "transformation", "using", "which", "with",
}


def ranking_signal(entry: dict[str, Any]) -> str:
    tags = entry.get("structured_tags") or {}
    if isinstance(tags, dict) and "value" in tags:
        tags = tags.get("value") or {}
    tag_text = " ".join(str(value) for value in tags.values()) if isinstance(tags, dict) else str(tags)
    return " ".join(
        [
            str(entry.get("title") or ""),
            str(entry.get("abstract") or ""),
            tag_text,
        ]
    ).lower()


def contract_ranking_terms(topic_contract: dict[str, Any] | None) -> list[str]:
    if not isinstance(topic_contract, dict):
        return []
    values = [
        topic_contract.get("topic"),
        topic_contract.get("central_question"),
        *(topic_contract.get("important_coverage") or []),
        *(topic_contract.get("inclusion_criteria") or []),
    ]
    tokens = tokenize(" ".join(str(value or "") for value in values))
    return [token for token in tokens if token not in RANKING_STOPWORDS]


def required_contract_terms(topic_contract: dict[str, Any] | None) -> list[str]:
    if not isinstance(topic_contract, dict):
        return []
    required: list[str] = []
    for criterion in topic_contract.get("exclusion_criteria") or []:
        text = str(criterion or "").lower()
        for pattern in (r"\b([a-z][a-z0-9-]{2,})\s+is not part\b",):
            for match in re.finditer(pattern, text):
                term = match.group(1).removesuffix("-mediated").removesuffix("-catalyzed")
                if term not in RANKING_STOPWORDS:
                    required.append(term)
    return dedupe(required)


def required_term_present(term: str, signal: str) -> bool:
    aliases = {
        "nickel": ("nickel", "ni-catalyzed", "ni catalyzed", "ni-mediated", "ni mediated"),
        "copper": ("copper", "cu-catalyzed", "cu catalyzed", "cu-mediated", "cu mediated"),
        "palladium": ("palladium", "pd-catalyzed", "pd catalyzed", "pd-mediated", "pd mediated"),
    }
    return any(alias in signal for alias in aliases.get(term, (term,)))


def finalize_local_ranking(
    entries: list[dict[str, Any]],
    topic_contract: dict[str, Any] | None,
) -> None:
    contract_terms = contract_ranking_terms(topic_contract)
    required_terms = required_contract_terms(topic_contract)
    coverage_phrases = (
        topic_contract.get("important_coverage") or []
        if isinstance(topic_contract, dict)
        else []
    )
    for entry in entries:
        scores = sorted(
            (float(value) for value in (entry.get("keyword_scores") or {}).values()),
            reverse=True,
        )
        aggregate = sum(score * weight for score, weight in zip(scores[:4], (1.0, 0.45, 0.25, 0.15)))
        signal = ranking_signal(entry)
        contract_hits = sorted({term for term in contract_terms if term in signal})
        required_misses = [term for term in required_terms if not required_term_present(term, signal)]
        coverage_hits = []
        for phrase in coverage_phrases:
            phrase_terms = [term for term in tokenize(str(phrase)) if term not in RANKING_STOPWORDS]
            if phrase_terms and sum(term in signal for term in phrase_terms) >= min(2, len(phrase_terms)):
                coverage_hits.append(str(phrase))
        category_count = len(set(entry.get("matched_keyword_categories") or []))
        ranking_score = (
            aggregate
            + min(len(contract_hits) * 0.025, 0.5)
            + min(category_count * 0.05, 0.2)
            + min(len(coverage_hits) * 0.04, 0.2)
            - min(len(required_misses) * 0.8, 1.6)
        )
        entry["aggregate_keyword_score"] = round(aggregate, 4)
        entry["contract_term_hits"] = contract_hits
        entry["important_coverage_hits"] = coverage_hits
        entry["required_contract_terms"] = required_terms
        entry["required_contract_term_misses"] = required_misses
        entry["ranking_score"] = round(ranking_score, 4)
        entry["ranking_reason"] = (
            "aggregate match across topic keywords, topic-contract terms, and coverage signals"
        )


def finalize_external_ranking(
    entries: list[dict[str, Any]],
    topic_contract: dict[str, Any] | None,
) -> None:
    """Add topic-contract and coverage signals to provider-ranked web rows.

    Provider relevance is useful for ranking one paper at a time, but a review
    corpus must also expose which parts of the declared scope each paper may
    help screen.  These fields remain metadata-level screening aids; they do
    not make a scientific selection or prove that the paper was read.
    """
    contract_terms = contract_ranking_terms(topic_contract)
    required_terms = required_contract_terms(topic_contract)
    coverage_phrases = (
        topic_contract.get("important_coverage") or []
        if isinstance(topic_contract, dict)
        else []
    )
    for entry in entries:
        signal = ranking_signal(entry)
        contract_hits = sorted({term for term in contract_terms if term in signal})
        required_misses = [term for term in required_terms if not required_term_present(term, signal)]
        coverage_hits = []
        for phrase in coverage_phrases:
            phrase_terms = [term for term in tokenize(str(phrase)) if term not in RANKING_STOPWORDS]
            if phrase_terms and sum(term in signal for term in phrase_terms) >= min(2, len(phrase_terms)):
                coverage_hits.append(str(phrase))
        try:
            provider_score = float(entry.get("score") or 0)
        except (TypeError, ValueError):
            provider_score = 0.0
        category_count = len(set(entry.get("matched_keyword_categories") or []))
        ranking_score = (
            provider_score
            + min(len(contract_hits) * 0.025, 0.5)
            + min(category_count * 0.05, 0.2)
            + min(len(coverage_hits) * 0.04, 0.2)
            - min(len(required_misses) * 0.8, 1.6)
        )
        title = str(entry.get("title") or "").lower()
        orientation_terms = (
            "review", "perspective", "accounts", "overview", "advances",
            "progress", "principles", "tutorial", "outlook",
        )
        entry["provider_score"] = round(provider_score, 4)
        entry["contract_term_hits"] = contract_hits
        entry["important_coverage_hits"] = coverage_hits
        entry["required_contract_terms"] = required_terms
        entry["required_contract_term_misses"] = required_misses
        entry["ranking_score"] = round(ranking_score, 4)
        entry["screening_role_hint"] = (
            "orientation_review"
            if any(term in title for term in orientation_terms)
            else "primary_or_specialist_candidate"
        )
        entry["ranking_reason"] = (
            "provider relevance plus topic-contract, query-category, and coverage signals"
        )


def selected_from_combined(
    combined: list[dict[str, Any]],
    max_local_candidates: int = 0,
    topic_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = {"keywords": [], "local_papers": {}, "web_papers": {}}
    for group in combined:
        if not group.get("keep", True):
            continue
        selected["keywords"].append({"keyword": group["keyword"], "category": group.get("category")})
        for result in group.get("local_results", []):
            if not result.get("keep", True):
                continue
            pid = result.get("paper_id")
            if not pid:
                continue
            entry = selected["local_papers"].setdefault(
                pid,
                {
                    "paper_id": pid,
                    "title": result.get("title"),
                    "year": result.get("year"),
                    "journal": result.get("journal"),
                    "abstract": result.get("abstract") or "",
                    "structured_tags": result.get("structured_tags") or {},
                    "source_paths": result.get("source_paths") or {},
                    "role": result.get("role", "uncertain"),
                    "matched_keywords": [],
                    "matched_keyword_categories": [],
                    "keyword_scores": {},
                    "best_score": 0,
                    "keep": True,
                },
            )
            entry["matched_keywords"].append(group["keyword"])
            category = str(group.get("category") or "uncategorized")
            if category not in entry["matched_keyword_categories"]:
                entry["matched_keyword_categories"].append(category)
            entry["keyword_scores"][group["keyword"]] = max(
                float(entry["keyword_scores"].get(group["keyword"], 0)),
                float(result.get("score", 0)),
            )
            entry["best_score"] = max(entry["best_score"], result.get("score", 0))
            if role_rank(result.get("role")) < role_rank(entry["role"]):
                entry["role"] = result.get("role")
        for result in group.get("web_results", []):
            if result.get("keep", True):
                key = _result_dedupe_key(result)
                entry = selected["web_papers"].setdefault(
                    key,
                    {
                        **result,
                        "matched_keyword": group["keyword"],
                        "matched_keywords": [],
                        "matched_keyword_categories": [],
                    },
                )
                if group["keyword"] not in entry["matched_keywords"]:
                    entry["matched_keywords"].append(group["keyword"])
                category = str(group.get("category") or "uncategorized")
                if category not in entry["matched_keyword_categories"]:
                    entry["matched_keyword_categories"].append(category)
    selected["local_papers"] = list(selected["local_papers"].values())
    finalize_local_ranking(selected["local_papers"], topic_contract)
    required_terms = required_contract_terms(topic_contract)
    if required_terms:
        selected["local_papers"] = [
            row
            for row in selected["local_papers"]
            if not row.get("required_contract_term_misses")
        ]
    selected["local_papers"].sort(
        key=lambda r: (r.get("ranking_score") or 0, r["best_score"], r.get("year") or 0),
        reverse=True,
    )
    selected["web_papers"] = list(selected["web_papers"].values())
    finalize_external_ranking(selected["web_papers"], topic_contract)
    selected["web_papers"].sort(
        key=lambda row: (
            row.get("ranking_score") or 0,
            row.get("provider_score") or row.get("score") or 0,
            row.get("year") or 0,
        ),
        reverse=True,
    )
    if max_local_candidates > 0:
        selected["local_papers"] = selected["local_papers"][:max_local_candidates]
    return selected


def role_rank(role: str | None) -> int:
    order = {"core_candidate": 0, "supporting_candidate": 1, "background": 2, "uncertain": 3, "excluded": 4}
    return order.get(role or "uncertain", 3)


def build_external_ingest_plan(
    project_id: str,
    web_papers: list[dict[str, Any]],
    external_requested: bool,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for row in web_papers:
        title = str(row.get("title") or "untitled external paper").strip()
        year = str(row.get("year") or "undated")
        external_id = str(row.get("external_id") or _normalized_doi(row.get("doi")) or slugify(title))
        local_paper_id = row.get("local_paper_id")
        pdf_url = str(row.get("open_access_pdf_url") or "").strip()
        full_text_url = str(row.get("open_access_full_text_url") or "").strip()
        repository_full_text_url = str(
            row.get("repository_full_text_url") or ""
        ).strip()
        stable_suffix = slugify(external_id)[-12:]
        # Keep the basename compact enough for MinerU's extracted image paths on Windows.
        target_relative = f"chem_papers/web-imports/{year}-{slugify(title)[:36]}-{stable_suffix}.pdf"
        if local_paper_id:
            action = "use_local"
            next_step = f"Use managed paper {local_paper_id}; do not download a duplicate."
        elif repository_full_text_url:
            action = "ingest_repository_full_text"
            next_step = (
                "Import the lawful repository full text with provenance; "
                "do not route structured JATS through MinerU."
            )
        elif pdf_url:
            action = "download_then_mineru"
            next_step = "Download the OA PDF, parse that one file with MinerU, then run metadata preparation."
        elif full_text_url:
            action = "locate_pdf"
            next_step = "A licensed publisher full-text page is known; locate its lawful PDF before evidence extraction."
        else:
            action = "locate_pdf"
            next_step = "Locate a lawful full-text PDF before evidence extraction."
        items.append(
            {
                "paper_key": external_id,
                "source": row.get("source"),
                "title": title,
                "authors": row.get("authors") or [],
                "year": row.get("year"),
                "journal": row.get("journal"),
                "doi": row.get("doi"),
                "abstract": row.get("abstract"),
                "matched_keywords": row.get("matched_keywords") or [row.get("matched_keyword")],
                "matched_keyword_categories": row.get("matched_keyword_categories") or [],
                "provider_score": row.get("provider_score", row.get("score")),
                "ranking_score": row.get("ranking_score", row.get("score")),
                "ranking_reason": row.get("ranking_reason"),
                "contract_term_hits": row.get("contract_term_hits") or [],
                "important_coverage_hits": row.get("important_coverage_hits") or [],
                "required_contract_term_misses": row.get("required_contract_term_misses") or [],
                "screening_role_hint": row.get("screening_role_hint"),
                "local_paper_id": local_paper_id,
                "open_access_pdf_url": pdf_url,
                "open_access_full_text_url": full_text_url,
                "repository_provider": row.get("repository_provider"),
                "repository_id": row.get("repository_id"),
                "repository_format": row.get("repository_format"),
                "repository_full_text_url": repository_full_text_url,
                "license": row.get("license"),
                "action": action,
                "target_pdf_path": (
                    target_relative
                    if action in {"download_then_mineru", "ingest_repository_full_text"}
                    else None
                ),
                "next_step": next_step,
            }
        )
    actionable = sum(item["action"] == "download_then_mineru" for item in items)
    repository_actionable = sum(
        item["action"] == "ingest_repository_full_text" for item in items
    )
    located = sum(
        bool(
            item.get("open_access_pdf_url")
            or item.get("open_access_full_text_url")
            or item.get("repository_full_text_url")
        )
        for item in items
    )
    if not external_requested:
        status = "disabled"
    elif not items:
        status = "no_candidates"
    elif actionable or repository_actionable:
        status = "ready"
    else:
        status = "metadata_only"
    return {
        "project_id": project_id,
        "status": status,
        "candidate_count": len(items),
        "downloadable_count": actionable,
        "repository_ingestible_count": repository_actionable,
        "importable_count": actionable + repository_actionable,
        "full_text_located_count": located,
        "items": items,
    }


def build_corpus_plan_draft(
    project_id: str,
    discovery_run_id: str,
    topic_contract: dict[str, Any] | None,
    ingest_plan: dict[str, Any],
) -> dict[str, Any]:
    """Expose a set-level screening surface between discovery and ingestion.

    The draft does not choose papers automatically.  It keeps every lawfully
    importable candidate visible, shows provisional coverage and literature
    roles, and gives the model one place to design a coherent reading corpus.
    """
    importable_actions = {"download_then_mineru", "ingest_repository_full_text"}
    candidates = []
    for item in ingest_plan.get("items") or []:
        if not isinstance(item, dict) or item.get("action") not in importable_actions:
            continue
        candidates.append(
            {
                key: item.get(key)
                for key in (
                    "paper_key",
                    "title",
                    "authors",
                    "year",
                    "journal",
                    "doi",
                    "abstract",
                    "action",
                    "source",
                    "provider_score",
                    "ranking_score",
                    "ranking_reason",
                    "matched_keywords",
                    "matched_keyword_categories",
                    "contract_term_hits",
                    "important_coverage_hits",
                    "required_contract_term_misses",
                    "screening_role_hint",
                )
            }
        )
    coverage_axes = []
    for axis in (
        topic_contract.get("important_coverage") or []
        if isinstance(topic_contract, dict)
        else []
    ):
        coverage_axes.append(
            {
                "axis": str(axis),
                "candidate_paper_keys": [
                    str(row.get("paper_key"))
                    for row in candidates
                    if str(axis) in (row.get("important_coverage_hits") or [])
                ],
            }
        )
    return {
        "report_type": "corpus_plan_draft",
        "project_id": project_id,
        "discovery_run_id": discovery_run_id,
        "central_question": (
            topic_contract.get("central_question")
            if isinstance(topic_contract, dict)
            else None
        ),
        "coverage_axes": coverage_axes,
        "orientation_candidate_paper_keys": [
            str(row.get("paper_key"))
            for row in candidates
            if row.get("screening_role_hint") == "orientation_review"
        ],
        "importable_candidate_count": len(candidates),
        "candidates": candidates,
        "selection": {
            "selected_paper_keys": [],
            "selection_rationale": "",
            "uncovered_or_deferred_axes": [],
        },
        "guidance": (
            "Screen this as a literature set, not as an ordered list of isolated papers. "
            "Choose a coherent initial corpus spanning orientation, primary evidence, "
            "comparisons, historical change, contrary evidence, and scope boundaries. "
            "Metadata and abstracts support screening only; ingestion makes selected full "
            "text available for reading and does not make it citable."
        ),
    }


def write_report(out_dir: Path, topic: str, keyword_set: dict[str, Any], combined: list[dict[str, Any]]) -> None:
    lines = ["# Topic Paper Discovery Report", "", f"Topic: {topic}", "", "## Keywords", ""]
    for kw in keyword_set["merged_keywords"]:
        lines.append(f"- {kw['keyword']} ({kw.get('category')}, source={'+'.join(kw.get('source', []))})")
    lines += ["", "## Results by Keyword", ""]
    for group in combined:
        lines.append(f"### {group['keyword']}")
        lines.append("")
        lines.append("Local:")
        for result in group.get("local_results", [])[:10]:
            lines.append(f"- `{result['paper_id']}` score={result['score']:.3f} role={result['role']} {result['title']}")
        if group.get("web_results"):
            lines.append("")
            lines.append("Web:")
            for result in group.get("web_results", [])[:8]:
                lines.append(f"- score={result['score']:.3f} {result['title']} {result.get('url') or ''}")
        lines.append("")
    (out_dir / "discovery_report.md").write_text("\n".join(lines), encoding="utf-8")


def _load_dotenv_if_present(review_root: Path) -> None:
    env_path = review_root / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
    except Exception:
        pass


def run(args: argparse.Namespace) -> int:
    review_root = Path(args.review_root).resolve()
    _load_dotenv_if_present(review_root)
    provider_explicit = bool(
        args.sciatlas_search or args.web_search or args.semantic_scholar_search
    )
    if args.local_only and provider_explicit:
        raise SystemExit("--local-only cannot be combined with external provider flags")
    contract_seed = load_topic_contract_file(args.topic_contract_file)
    topic = str(
        args.topic
        or contract_seed.get("retrieval_query")
        or contract_seed.get("topic")
        or contract_seed.get("manuscript_title")
        or ""
    ).strip()
    if not topic:
        raise SystemExit("Provide --topic or --topic-contract-file with a retrieval query/topic")
    central_question = str(
        args.central_question or contract_seed.get("central_question") or topic
    ).strip()
    important_coverage = dedupe(
        args.important_coverage or list(contract_seed.get("important_coverage") or [])
    )
    inclusion_criteria = dedupe(
        args.inclusion_criterion or list(contract_seed.get("inclusion_criteria") or [])
    )
    exclusion_criteria = dedupe(
        args.exclusion_criterion or list(contract_seed.get("exclusion_criteria") or [])
    )
    keyword_seed = args.keywords or ", ".join(contract_seed.get("suggested_keywords") or [])
    user_keywords = split_keywords(keyword_seed)
    manuscript_title = str(contract_seed.get("manuscript_title") or "").strip()
    project_manifest = ensure_review_project(
        review_root,
        topic,
        title=manuscript_title,
        project_id=args.project_id,
    )
    project_id = str(project_manifest["project_id"])
    project = review_root / "review-projects" / project_id
    try:
        process_lock_path = acquire_discovery_process_lock(project, project_id)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    def cleanup_discovery_process_lock() -> None:
        release_discovery_process_lock(process_lock_path)

    atexit.register(cleanup_discovery_process_lock)
    progress(f"starting project {project_id}: {topic}")
    prior_manifest = read_json(project / "project.json")
    prior_run_id = str(prior_manifest.get("current_discovery_run_id") or "").strip()
    prior_runs = [
        row
        for row in prior_manifest.get("discovery_runs", [])
        if isinstance(row, dict)
    ]
    prior_record = next(
        (
            row
            for row in prior_runs
            if str(row.get("discovery_run_id") or "") == prior_run_id
        ),
        None,
    )
    if prior_run_id and prior_record and prior_record.get("status") == "in_progress":
        record_discovery_run(
            project,
            prior_run_id,
            str(prior_record.get("topic") or topic),
            "interrupted",
        )
        progress(f"marked prior discovery run interrupted: {prior_run_id}")
    archived_discovery = archive_current_discovery(project)
    out_dir = project / "00_discovery"
    out_dir.mkdir(parents=True, exist_ok=True)
    discovery_run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + f"-{os.getpid()}"
    )
    record_discovery_run(project, discovery_run_id, topic, "in_progress")
    in_progress_path = out_dir / ".discovery_in_progress.json"
    write_json(
        in_progress_path,
        {
            "project_id": project_id,
            "discovery_run_id": discovery_run_id,
            "started_at": utc_now(),
            "status": "in_progress",
        },
    )
    def cleanup_discovery_marker() -> None:
        in_progress_path.unlink(missing_ok=True)

    atexit.register(cleanup_discovery_marker)
    topic_contract = {
        "discovery_run_id": discovery_run_id,
        "topic": topic,
        "central_question": central_question,
        "important_coverage": important_coverage,
        "inclusion_criteria": inclusion_criteria,
        "exclusion_criteria": exclusion_criteria,
    }
    review_profile = str(contract_seed.get("review_profile") or "").strip().lower()
    if review_profile:
        topic_contract["review_profile"] = review_profile
    quality_requirements = contract_seed.get("quality_requirements")
    if isinstance(quality_requirements, dict):
        topic_contract["quality_requirements"] = quality_requirements
    if manuscript_title:
        topic_contract["manuscript_title"] = manuscript_title
    retrieval_query = str(contract_seed.get("retrieval_query") or "").strip()
    if retrieval_query:
        topic_contract["retrieval_query"] = retrieval_query
    write_json(out_dir / "topic_contract.json", topic_contract)
    topic_lines = [
        f"# {manuscript_title or topic}",
        "",
        "## Central question",
        "",
        topic_contract["central_question"],
        "",
        "## Important coverage",
        "",
    ]
    topic_lines.extend(f"- {item}" for item in topic_contract["important_coverage"])
    topic_lines.extend(["", "## Inclusion criteria", ""])
    topic_lines.extend(f"- {item}" for item in topic_contract["inclusion_criteria"])
    topic_lines.extend(["", "## Exclusion criteria", ""])
    topic_lines.extend(f"- {item}" for item in topic_contract["exclusion_criteria"])
    topic_lines.extend(["", "## User keywords", ""])
    topic_lines.extend(f"- {kw}" for kw in user_keywords)
    (out_dir / "topic_input.md").write_text("\n".join(topic_lines).rstrip() + "\n", encoding="utf-8")
    keyword_set = build_keyword_set(topic, user_keywords, topic_contract)
    keyword_set["discovery_run_id"] = discovery_run_id
    write_json(out_dir / "keyword_set.draft.json", keyword_set)
    progress(
        "query plan ready: "
        f"{len(keyword_set['merged_keywords'])} keyword groups, "
        f"{len(important_coverage)} declared coverage axes"
    )
    papers = load_metadata(review_root)
    classification_rules = load_classification_rules(review_root, topic)
    local_grouped = local_search_by_keyword(
        papers,
        keyword_set["merged_keywords"],
        topic,
        classification_rules,
        review_root,
    )
    write_json(out_dir / "local_results_by_keyword.json", {"project_id": project_id, "results": local_grouped})
    local_hits = sum(len(group.get("local_results") or []) for group in local_grouped)
    progress(f"local library search complete: {len(papers)} registered papers, {local_hits} grouped hits")
    sciatlas_requested = bool(args.sciatlas_search) and not args.local_only
    europe_pmc_requested = (
        not args.local_only and not args.no_europe_pmc_search
    )
    # Crossref is the credential-free default. Semantic Scholar is an optional
    # enrichment layer and never replaces the Crossref metadata/OA path.
    crossref_requested = (
        bool(args.web_search) or not bool(args.sciatlas_search)
    ) and not args.local_only
    semantic_scholar_requested = bool(args.semantic_scholar_search) and not args.local_only
    external_requested = (
        sciatlas_requested
        or europe_pmc_requested
        or crossref_requested
        or semantic_scholar_requested
    )
    sciatlas_client: SciAtlasClient | None = None
    sciatlas_status = "disabled"
    if sciatlas_requested:
        config = load_config(
            base_url=args.sciatlas_base_url or None,
            api_key=args.sciatlas_api_key or None,
            timeout=args.sciatlas_timeout or None,
        )
        if not config.configured:
            sciatlas_status = "missing_api_key"
        else:
            sciatlas_client = SciAtlasClient(config=config)
            try:
                sciatlas_client.health()
                sciatlas_status = "ok"
            except Exception as exc:
                sciatlas_status = f"health_failed: {exc}"
                sciatlas_client = None

    semantic_scholar_api_key = args.semantic_scholar_api_key or os.environ.get(
        "SEMANTIC_SCHOLAR_API_KEY", ""
    )
    semantic_scholar_active = semantic_scholar_requested
    europe_pmc_active = europe_pmc_requested
    external_grouped: list[dict[str, Any]] = []
    sources_used: list[str] = []
    provider_errors: dict[str, list[str]] = {
        "sciatlas": [],
        "europe_pmc": [],
        "semantic_scholar": [],
        "crossref": [],
    }
    provider_stats: dict[str, dict[str, int]] = {
        name: {
            "attempted_queries": 0,
            "successful_queries": 0,
            "returned_records": 0,
            "retained_records": 0,
        }
        for name in provider_errors
    }
    queried_groups = choose_external_groups(local_grouped, args.external_query_limit)
    progress(
        "external retrieval starting: "
        f"{len(queried_groups)} planned queries; "
        f"Europe PMC={'on' if europe_pmc_active else 'off'}, "
        f"Crossref={'on' if crossref_requested else 'off'}, "
        f"Semantic Scholar={'on' if semantic_scholar_active else 'off'}, "
        f"SciAtlas={'on' if sciatlas_client is not None else 'off'}"
    )
    for group_index, group in enumerate(queried_groups, start=1):
        rows: list[dict[str, Any]] = []
        progress(
            f"external query {group_index}/{len(queried_groups)}: {group['keyword']}"
        )
        if europe_pmc_active:
            progress(f"query {group_index}/{len(queried_groups)} Europe PMC request started")
            provider_stats["europe_pmc"]["attempted_queries"] += 1
            raw_europe_pmc_rows = europe_pmc_search(
                group["keyword"],
                topic,
                args.europe_pmc_limit,
            )
            europe_pmc_rows, europe_pmc_errors = split_provider_rows(
                raw_europe_pmc_rows
            )
            rows.extend(europe_pmc_rows)
            provider_errors["europe_pmc"].extend(europe_pmc_errors)
            if not europe_pmc_errors:
                provider_stats["europe_pmc"]["successful_queries"] += 1
            provider_stats["europe_pmc"]["returned_records"] += len(
                europe_pmc_rows
            )
            provider_stats["europe_pmc"]["retained_records"] += sum(
                bool(row.get("keep", True)) for row in europe_pmc_rows
            )
            if (
                any(row.get("keep", True) for row in europe_pmc_rows)
                and "europe_pmc" not in sources_used
            ):
                sources_used.append("europe_pmc")
            if europe_pmc_errors:
                europe_pmc_active = False
            progress(
                f"query {group_index}/{len(queried_groups)} Europe PMC complete: "
                f"returned={len(europe_pmc_rows)} errors={len(europe_pmc_errors)}"
            )
            if args.web_delay:
                time.sleep(args.web_delay)
        if sciatlas_client is not None:
            progress(f"query {group_index}/{len(queried_groups)} SciAtlas request started")
            provider_stats["sciatlas"]["attempted_queries"] += 1
            raw_sciatlas_rows = sciatlas_search(
                sciatlas_client,
                group["keyword"],
                topic,
                args.sciatlas_limit,
                args.sciatlas_time_range or None,
                args.sciatlas_domain or None,
            )
            sciatlas_rows, sciatlas_errors = split_provider_rows(raw_sciatlas_rows)
            rows.extend(sciatlas_rows)
            provider_errors["sciatlas"].extend(sciatlas_errors)
            if not sciatlas_errors:
                provider_stats["sciatlas"]["successful_queries"] += 1
            provider_stats["sciatlas"]["returned_records"] += len(sciatlas_rows)
            provider_stats["sciatlas"]["retained_records"] += sum(
                bool(row.get("keep", True)) for row in sciatlas_rows
            )
            if any(row.get("keep", True) for row in sciatlas_rows) and "sciatlas" not in sources_used:
                sources_used.append("sciatlas")
            progress(
                f"query {group_index}/{len(queried_groups)} SciAtlas complete: "
                f"returned={len(sciatlas_rows)} errors={len(sciatlas_errors)}"
            )
            if args.web_delay:
                time.sleep(args.web_delay)
        if semantic_scholar_active:
            progress(f"query {group_index}/{len(queried_groups)} Semantic Scholar request started")
            provider_stats["semantic_scholar"]["attempted_queries"] += 1
            raw_semantic_rows = semantic_scholar_search(
                group["keyword"],
                topic,
                args.semantic_scholar_limit,
                semantic_scholar_api_key,
            )
            semantic_rows, semantic_errors = split_provider_rows(raw_semantic_rows)
            rows.extend(semantic_rows)
            provider_errors["semantic_scholar"].extend(semantic_errors)
            if not semantic_errors:
                provider_stats["semantic_scholar"]["successful_queries"] += 1
            provider_stats["semantic_scholar"]["returned_records"] += len(semantic_rows)
            provider_stats["semantic_scholar"]["retained_records"] += sum(
                bool(row.get("keep", True)) for row in semantic_rows
            )
            if any(row.get("keep", True) for row in semantic_rows) and "semantic_scholar" not in sources_used:
                sources_used.append("semantic_scholar")
            if semantic_errors:
                # A shared-pool throttle or provider outage will affect the
                # remaining keyword calls too. Stop hammering it and let the
                # independent Crossref path continue.
                semantic_scholar_active = False
            progress(
                f"query {group_index}/{len(queried_groups)} Semantic Scholar complete: "
                f"returned={len(semantic_rows)} errors={len(semantic_errors)}"
            )
            if args.web_delay:
                time.sleep(args.web_delay)
        if crossref_requested:
            progress(f"query {group_index}/{len(queried_groups)} Crossref request started")
            provider_stats["crossref"]["attempted_queries"] += 1
            raw_crossref_rows = web_search(group["keyword"], topic, args.web_limit)
            crossref_rows, crossref_errors = split_provider_rows(raw_crossref_rows)
            rows.extend(crossref_rows)
            provider_errors["crossref"].extend(crossref_errors)
            if not crossref_errors:
                provider_stats["crossref"]["successful_queries"] += 1
            provider_stats["crossref"]["returned_records"] += len(crossref_rows)
            provider_stats["crossref"]["retained_records"] += sum(
                bool(row.get("keep", True)) for row in crossref_rows
            )
            if any(row.get("keep", True) for row in crossref_rows) and "crossref" not in sources_used:
                sources_used.append("crossref")
            progress(
                f"query {group_index}/{len(queried_groups)} Crossref complete: "
                f"returned={len(crossref_rows)} errors={len(crossref_errors)}"
            )
            if args.web_delay:
                time.sleep(args.web_delay)
        merged = annotate_external_results(merge_external_results(rows), papers)
        if merged:
            external_grouped.append({"keyword": group["keyword"], "web_results": merged})
        progress(
            f"external query {group_index}/{len(queried_groups)} merged: {len(merged)} unique candidates"
        )

    reference_expansion: dict[str, Any] = {
        "status": "disabled",
        "seed_query": "",
        "seeds": [],
        "results": [],
        "errors": [],
    }
    if crossref_requested and args.reference_expansion_limit > 0:
        progress("reference expansion starting")
        seed_hints = review_seed_hints_from_grouped(external_grouped)
        reference_expansion = crossref_reference_expansion(
            topic,
            [group["keyword"] for group in queried_groups],
            seed_limit=args.review_seed_limit,
            result_limit=args.reference_expansion_limit,
            seed_hints=seed_hints,
            progress_callback=progress,
        )
        expansion_rows = annotate_external_results(
            reference_expansion.get("results") or [],
            papers,
        )
        reference_expansion["results"] = expansion_rows
        if expansion_rows:
            external_grouped.append(
                {
                    "keyword": "review reference expansion",
                    "category": "document_scope",
                    "web_results": expansion_rows,
                }
            )
            if "crossref_reference_expansion" not in sources_used:
                sources_used.append("crossref_reference_expansion")
        progress(
            "reference expansion complete: "
            f"status={reference_expansion.get('status')} "
            f"retained={len(expansion_rows)}"
        )
    unpaywall_email = (
        args.unpaywall_email
        or os.environ.get("UNPAYWALL_EMAIL", "")
    )
    progress("open-access resolution starting")
    oa_resolution = enrich_grouped_open_access(
        external_grouped,
        unpaywall_email,
        args.oa_resolution_limit,
        progress_callback=progress,
    )
    progress(
        "open-access resolution complete: "
        f"status={oa_resolution.get('status')} "
        f"attempted={oa_resolution.get('attempted')} "
        f"resolved={oa_resolution.get('resolved')}"
    )
    write_json(out_dir / "reference_expansion.json", reference_expansion)

    if sciatlas_requested and sciatlas_client is None and not (
        europe_pmc_requested or crossref_requested or semantic_scholar_requested
    ):
        external_status = sciatlas_status
    elif external_requested and sources_used:
        external_status = "+".join(sources_used)
        if sciatlas_requested and sciatlas_client is None:
            external_status = f"sciatlas_unavailable({sciatlas_status}); {external_status}"
    elif external_requested:
        external_status = "requested_but_no_results"
    else:
        external_status = "disabled"

    if not sources_used:
        external_source = "none"
    elif len(sources_used) == 1:
        external_source = sources_used[0]
    else:
        external_source = "+".join(sources_used)

    write_json(out_dir / "web_results_by_keyword.json", {
        "project_id": project_id,
        "local_only": bool(args.local_only),
        "enabled": external_requested,
        "source": external_source,
        "status": external_status,
        "sources": sources_used,
        "queried_keywords": [group["keyword"] for group in queried_groups] if external_requested else [],
        "external_query_limit": args.external_query_limit,
        "reference_expansion": {
            key: value
            for key, value in reference_expansion.items()
            if key != "results"
        },
        "providers": {
            "sciatlas": {
                "requested": sciatlas_requested,
                "status": provider_run_status(
                    sciatlas_requested,
                    provider_stats["sciatlas"],
                    provider_errors["sciatlas"],
                    sciatlas_status if sciatlas_client is None else "",
                ),
                **provider_stats["sciatlas"],
            },
            "europe_pmc": {
                "requested": europe_pmc_requested,
                "status": provider_run_status(
                    europe_pmc_requested,
                    provider_stats["europe_pmc"],
                    provider_errors["europe_pmc"],
                ),
                **provider_stats["europe_pmc"],
            },
            "semantic_scholar": {
                "requested": semantic_scholar_requested,
                "status": provider_run_status(
                    semantic_scholar_requested,
                    provider_stats["semantic_scholar"],
                    provider_errors["semantic_scholar"],
                ),
                **provider_stats["semantic_scholar"],
            },
            "crossref": {
                "requested": crossref_requested,
                "status": provider_run_status(
                    crossref_requested,
                    provider_stats["crossref"],
                    provider_errors["crossref"],
                ),
                **provider_stats["crossref"],
            },
        },
        "oa_resolution": oa_resolution,
        "provider_errors": {key: dedupe(values) for key, values in provider_errors.items() if values},
        "results": external_grouped,
    })
    web_grouped = external_grouped
    combined = combine_results(local_grouped, web_grouped)
    write_json(out_dir / "combined_results_by_keyword.json", {"project_id": project_id, "topic": topic, "results": combined})
    selected = selected_from_combined(
        combined,
        max_local_candidates=args.max_local_candidates,
        topic_contract=topic_contract,
    )
    selected["project_id"] = project_id
    selected["discovery_run_id"] = discovery_run_id
    selected["candidate_paper_ids"] = [row["paper_id"] for row in selected.get("local_papers", [])]
    selected["topic_contract"] = topic_contract
    write_json(out_dir / "selected_discovery_results.json", selected)
    ingest_plan = build_external_ingest_plan(
        project_id,
        selected.get("web_papers", []),
        external_requested,
    )
    ingest_plan["discovery_run_id"] = discovery_run_id
    write_json(out_dir / "external_ingest_plan.json", ingest_plan)
    write_json(
        out_dir / "corpus_plan.draft.json",
        build_corpus_plan_draft(
            project_id,
            discovery_run_id,
            topic_contract,
            ingest_plan,
        ),
    )
    progress(
        "writing discovery artifacts complete: "
        f"{len(selected.get('web_papers') or [])} external candidates, "
        f"{len(selected.get('local_papers') or [])} local candidates"
    )
    write_report(out_dir, topic, keyword_set, combined)
    cleanup_discovery_marker()
    atexit.unregister(cleanup_discovery_marker)
    record_discovery_run(project, discovery_run_id, topic, "completed")
    cleanup_discovery_process_lock()
    atexit.unregister(cleanup_discovery_process_lock)
    print(f"Discovery project: {project}")
    if archived_discovery:
        print(f"Previous discovery archived: {archived_discovery}")
    print(f"Keyword set: {out_dir / 'keyword_set.draft.json'}")
    print(f"Discovery review: http://127.0.0.1:8765/discovery")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover local and web papers by expanded topic keywords.")
    parser.add_argument("--review-root", default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument(
        "--project-id",
        default="",
        help=(
            "Existing or explicit project ID. Omit it to allocate the next "
            "CVR-0001-topic style ID."
        ),
    )
    parser.add_argument("--topic", default="")
    parser.add_argument(
        "--topic-contract-file",
        default="",
        help="JSON or structured Markdown contract; explicit CLI values override matching fields.",
    )
    parser.add_argument("--keywords", default="")
    parser.add_argument("--central-question", default="")
    parser.add_argument("--important-coverage", action="append", default=[])
    parser.add_argument("--inclusion-criterion", action="append", default=[])
    parser.add_argument("--exclusion-criterion", action="append", default=[])
    parser.add_argument(
        "--max-local-candidates",
        type=int,
        default=0,
        help="Optional explicit cap after scoring; 0 keeps every qualified local candidate.",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Explicitly disable all external coverage providers.",
    )
    parser.add_argument("--web-search", action="store_true", help="Query Crossref explicitly; Crossref is the default when no alternative provider is named.")
    parser.add_argument("--web-limit", type=int, default=8)
    parser.add_argument("--web-delay", type=float, default=0.2)
    parser.add_argument(
        "--no-europe-pmc-search",
        action="store_true",
        help="Disable the default Europe PMC search for ingestible open full text.",
    )
    parser.add_argument(
        "--europe-pmc-limit",
        type=int,
        default=12,
        help="Maximum topic-screened Europe PMC records retained per query.",
    )
    parser.add_argument(
        "--external-query-limit",
        type=int,
        default=0,
        help=(
            "Optional maximum expanded keywords sent to each external provider; "
            "the default 0 keeps every planned facet."
        ),
    )
    parser.add_argument(
        "--semantic-scholar-search",
        action="store_true",
        help="Optionally enrich Crossref results with Semantic Scholar metadata and OA-PDF locations.",
    )
    parser.add_argument("--semantic-scholar-limit", type=int, default=8)
    parser.add_argument(
        "--review-seed-limit",
        type=int,
        default=2,
        help="Maximum close reviews used for Crossref reference expansion.",
    )
    parser.add_argument(
        "--reference-expansion-limit",
        type=int,
        default=30,
        help="Maximum topic-screened review references retained; 0 disables expansion.",
    )
    parser.add_argument(
        "--semantic-scholar-api-key",
        default="",
        help="Optional override for SEMANTIC_SCHOLAR_API_KEY; unauthenticated search is supported.",
    )
    parser.add_argument(
        "--unpaywall-email",
        default="",
        help="Optional real contact email for Unpaywall; defaults to UNPAYWALL_EMAIL.",
    )
    parser.add_argument(
        "--oa-resolution-limit",
        type=int,
        default=40,
        help="Maximum unresolved unique DOIs sent to an optional OA resolver.",
    )
    parser.add_argument("--sciatlas-search", action="store_true", help="Query the hosted SciAtlas KG /v1/search per keyword.")
    parser.add_argument("--sciatlas-limit", type=int, default=8)
    parser.add_argument("--sciatlas-api-key", default="", help="Overrides SCIATLAS_API_KEY env var.")
    parser.add_argument("--sciatlas-base-url", default="", help="Overrides SCIATLAS_API_BASE_URL env var.")
    parser.add_argument("--sciatlas-timeout", type=int, default=0, help="HTTP timeout in seconds. 0 = use env/default.")
    parser.add_argument("--sciatlas-time-range", default="", help="Optional year range like 2018-2025.")
    parser.add_argument("--sciatlas-domain", default="", help="Optional domain hint, e.g. 'organic chemistry'.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
