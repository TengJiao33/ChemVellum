#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def resolve_path(review_root: Path, raw: Any) -> Path | None:
    value = str(raw or "").strip()
    if not value:
        return None
    path = Path(value)
    candidate = path if path.is_absolute() else review_root / path
    candidate = candidate.resolve()
    return candidate if candidate.is_file() else None


def snippet(text: str, terms: list[str], width: int = 260) -> str:
    lowered = text.lower()
    positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
    start = max(0, (min(positions) if positions else 0) - width // 3)
    value = re.sub(r"\s+", " ", text[start : start + width]).strip()
    if start:
        value = "..." + value
    if start + width < len(text):
        value += "..."
    return value


def search(
    review_root: Path,
    query: str,
    *,
    limit: int,
    metadata_only: bool,
) -> list[dict[str, Any]]:
    terms = [
        term.lower()
        for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", query)
        if len(term) > 1
    ]
    if not terms:
        raise ValueError("Query contains no searchable terms")
    metadata_dir = review_root / "review-library" / "metadata" / "papers"
    results: list[dict[str, Any]] = []
    for metadata_path in sorted(metadata_dir.glob("P*.metadata.json")):
        try:
            metadata = read_json(metadata_path)
        except (OSError, json.JSONDecodeError):
            continue
        title = str(unwrap(metadata.get("title")) or "")
        abstract = str(unwrap(metadata.get("abstract")) or "")
        title_lower = title.lower()
        abstract_lower = abstract.lower()
        score = sum(6 for term in terms if term in title_lower)
        score += sum(2 for term in terms if term in abstract_lower)
        matched_fields: list[str] = []
        if any(term in title_lower for term in terms):
            matched_fields.append("title")
        if any(term in abstract_lower for term in terms):
            matched_fields.append("abstract")

        full_text = ""
        source_path: Path | None = None
        if not metadata_only:
            source_paths = metadata.get("source_paths")
            source_paths = source_paths if isinstance(source_paths, dict) else {}
            source_path = (
                resolve_path(review_root, source_paths.get("markdown"))
                or resolve_path(review_root, source_paths.get("xml"))
            )
            if source_path:
                try:
                    full_text = source_path.read_text(
                        encoding="utf-8", errors="replace"
                    )[:750_000]
                except OSError:
                    full_text = ""
                full_lower = full_text.lower()
                hits = sum(full_lower.count(term) for term in terms)
                score += min(hits, 20)
                if hits:
                    matched_fields.append("full_text")

        if score <= 0:
            continue
        context = full_text or abstract or title
        results.append(
            {
                "paper_id": str(metadata.get("paper_id") or metadata_path.stem.split(".")[0]),
                "title": title,
                "year": unwrap(metadata.get("year")),
                "journal": unwrap(metadata.get("journal")),
                "doi": unwrap(metadata.get("doi")),
                "score": score,
                "matched_fields": matched_fields,
                "source_path": str(source_path) if source_path else None,
                "snippet": snippet(context, terms),
            }
        )
    results.sort(key=lambda row: (-int(row["score"]), str(row["paper_id"])))
    return results[:limit]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search managed review metadata and local full text."
    )
    parser.add_argument(
        "--review-root", default=str(Path(__file__).resolve().parents[3])
    )
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--output", help="Optional JSON output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    review_root = Path(args.review_root).resolve()
    try:
        rows = search(
            review_root,
            args.query,
            limit=max(1, args.limit),
            metadata_only=args.metadata_only,
        )
    except ValueError as exc:
        raise SystemExit(str(exc))
    payload = {"query": args.query, "result_count": len(rows), "results": rows}
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
