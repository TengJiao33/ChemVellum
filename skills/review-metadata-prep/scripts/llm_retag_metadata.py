#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from prepare_metadata import (
    STRUCTURED_TAG_KEYS,
    apply_structured_tags_to_compat_fields,
    build_llm_payload,
    load_dotenv,
    load_classification_rules,
    load_blocks,
    markdown_head,
    merge_llm,
    read_json,
    update_quality,
    write_json,
)


def call_responses(payload: dict[str, Any], api_key: str, base_url: str, timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "chemvellum-metadata-prep/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data.get("output_text")
    if not text:
        parts = []
        for item in data.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                    if content.get("text"):
                        parts.append(content["text"])
        text = "\n".join(parts)
    if not text:
        raise RuntimeError("response missing output_text")
    return json.loads(text)


def resolve_source_path(review_root: Path, value: Any) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else review_root / path


def retag_one(
    meta_path: Path,
    system_prompt: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
    reasoning_effort: str,
    classification_labels: dict[str, list[str]],
) -> dict[str, Any]:
    meta = read_json(meta_path)
    review_root = meta_path.resolve().parents[3]
    source_paths = meta.get("source_paths") or {}
    content_path = resolve_source_path(review_root, source_paths.get("content_list"))
    markdown_path = resolve_source_path(review_root, source_paths.get("markdown"))
    blocks = load_blocks(content_path if content_path and content_path.exists() else None)
    md = markdown_head(markdown_path if markdown_path and markdown_path.exists() else None)
    payload = build_llm_payload(meta, blocks, md, system_prompt, model, reasoning_effort, classification_labels)
    llm_data = call_responses(payload, api_key, base_url, timeout)
    merge_llm(meta, llm_data)
    apply_structured_tags_to_compat_fields(meta)
    meta.setdefault("extraction", {}).setdefault("notes", [])
    meta["extraction"]["mode"] = "llm_optional_chemistry_descriptors"
    meta["extraction"]["model"] = model
    meta["extraction"]["notes"].append("llm_optional_chemistry_descriptors_refreshed")
    update_quality(meta)
    write_json(meta_path, meta)
    return {
        "paper_id": meta.get("paper_id"),
        "metadata_path": str(meta_path),
        "status": "ok",
        "structured_tags": (meta.get("structured_tags") or {}).get("value"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh existing metadata with optional LLM chemistry descriptors.")
    parser.add_argument("--review-root", default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--reasoning-effort", default="", choices=["", "none", "low", "medium", "high"])
    parser.add_argument("--paper-id", action="append", default=[], help="Retag only selected paper_id. Repeatable.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--classification-rules",
        default="",
        help="Optional project-specific label rules. Omit for generic descriptors.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    review_root = Path(args.review_root).resolve()
    load_dotenv(review_root / ".env")
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
    model = args.model or os.environ.get("REVIEW_METADATA_MODEL", "gpt-5.4")
    reasoning_effort = args.reasoning_effort or os.environ.get("REVIEW_METADATA_REASONING_EFFORT", "high")
    if not api_key:
        raise SystemExit(f"Missing API key. Pass --api-key, set OPENAI_API_KEY, or write it to {review_root / '.env'}.")
    skill_root = Path(__file__).resolve().parents[1]
    system_prompt = (skill_root / "references" / "metadata_extraction_system.md").read_text(encoding="utf-8")
    classification_labels = (
        load_classification_rules(Path(args.classification_rules).resolve())
        if args.classification_rules
        else {}
    )
    meta_dir = review_root / "review-library" / "metadata" / "papers"
    paths = sorted(meta_dir.glob("*.metadata.json"))
    if args.paper_id:
        wanted = set(args.paper_id)
        paths = [p for p in paths if p.stem.replace(".metadata", "") in wanted]
    if args.limit > 0:
        paths = paths[: args.limit]
    reports = []
    for path in paths:
        try:
            report = retag_one(path, system_prompt, api_key, base_url, model, args.timeout, reasoning_effort, classification_labels)
            print(f"{report['paper_id']} ok")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            report = {
                "paper_id": path.stem.replace(".metadata", ""),
                "metadata_path": str(path),
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(f"{report['paper_id']} failed: {report['error']}")
        reports.append(report)
        if args.sleep_seconds:
            time.sleep(args.sleep_seconds)
    out = review_root / "review-library" / "metadata" / "llm_retag_report.json"
    write_json(out, {"total": len(reports), "failed": sum(1 for r in reports if r["status"] != "ok"), "reports": reports})
    write_markdown_report(out.with_suffix(".md"), reports)
    print(f"Wrote {out}")
    return 1 if any(r["status"] != "ok" for r in reports) else 0


def write_markdown_report(path: Path, reports: list[dict[str, Any]]) -> None:
    failed = [r for r in reports if r.get("status") != "ok"]
    lines = [
        "# LLM Retag Report",
        "",
        f"- Total: {len(reports)}",
        f"- Failed: {len(failed)}",
        "",
        "## Failures",
        "",
    ]
    if not failed:
        lines.append("None.")
    for row in failed:
        lines.append(f"- {row.get('paper_id')}: {row.get('error')}")
    lines += ["", "## Sample Tags", ""]
    for row in [r for r in reports if r.get("status") == "ok"][:30]:
        tags = row.get("structured_tags") or {}
        compact = "; ".join(f"{key}: {tags.get(key, '')}" for key in STRUCTURED_TAG_KEYS)
        lines.append(f"- {row.get('paper_id')}: {compact}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
