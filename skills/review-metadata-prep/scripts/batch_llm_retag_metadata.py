#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
from pathlib import Path
from typing import Any

from llm_retag_metadata import retag_one, write_markdown_report
from prepare_metadata import STRUCTURED_TAG_KEYS, load_classification_rules, load_dotenv, read_json, write_json


def field_value(meta: dict[str, Any], key: str) -> Any:
    value = meta.get(key)
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def has_complete_llm_tags(meta: dict[str, Any]) -> bool:
    structured = meta.get("structured_tags")
    structured_value = structured.get("value") if isinstance(structured, dict) else None
    if not isinstance(structured_value, dict):
        return False
    if set(structured_value) != set(STRUCTURED_TAG_KEYS):
        return False
    for key in STRUCTURED_TAG_KEYS:
        value = str(structured_value.get(key) or "").strip()
        if not value:
            return False
    extraction = meta.get("extraction") or {}
    if extraction.get("mode") in {
        "llm_8_category_retag",
        "llm_optional_chemistry_descriptors",
    }:
        return True
    source = str(structured.get("source") or "").lower()
    return source.startswith("llm")


def paper_id_from_path(path: Path) -> str:
    return path.stem.replace(".metadata", "")


def selected_paths(meta_dir: Path, paper_ids: list[str]) -> list[Path]:
    paths = sorted(meta_dir.glob("*.metadata.json"))
    if not paper_ids:
        return paths
    wanted = set(paper_ids)
    return [path for path in paths if paper_id_from_path(path) in wanted]


def pending_paths(paths: list[Path], force: bool) -> list[Path]:
    if force:
        return paths
    pending: list[Path] = []
    for path in paths:
        try:
            meta = read_json(path)
        except Exception:
            pending.append(path)
            continue
        if not isinstance(meta, dict) or not has_complete_llm_tags(meta):
            pending.append(path)
    return pending


def format_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            body = ""
        message = f"HTTPError {exc.code}: {exc.reason}"
        if body:
            message += f"; body={body[:1000]}"
        return message
    return f"{type(exc).__name__}: {exc}"


def write_progress(out_dir: Path, reports: list[dict[str, Any]], attempts: dict[str, int], total_target: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = [r for r in reports if r.get("status") == "ok"]
    failed = [r for r in reports if r.get("status") != "ok"]
    payload = {
        "total_target": total_target,
        "processed": len(reports),
        "ok": len(ok),
        "failed_events": len(failed),
        "attempts": attempts,
        "reports": reports,
    }
    write_json(out_dir / "llm_retag_batch_report.json", payload)
    write_markdown_report(out_dir / "llm_retag_batch_report.md", reports)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-refresh metadata with optional LLM chemistry descriptors, three papers per round by default."
    )
    parser.add_argument("--review-root", default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--reasoning-effort", default="", choices=["", "none", "low", "medium", "high"])
    parser.add_argument("--paper-id", action="append", default=[], help="Retag only selected paper_id. Repeatable.")
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--retry-forever", action="store_true")
    parser.add_argument("--retry-delay", type=float, default=30.0)
    parser.add_argument("--sleep-seconds", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--force", action="store_true", help="Retag papers even if they already have complete LLM tags.")
    parser.add_argument(
        "--classification-rules",
        default="",
        help="Optional project-specific label rules. Omit for generic descriptors.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be >= 1")
    if args.max_attempts < 1:
        raise SystemExit("--max-attempts must be >= 1")

    review_root = Path(args.review_root).resolve()
    load_dotenv(review_root / ".env")
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
    model = args.model or os.environ.get("REVIEW_METADATA_MODEL", "gpt-5.4")
    reasoning_effort = args.reasoning_effort or os.environ.get("REVIEW_METADATA_REASONING_EFFORT", "high")
    if not api_key:
        raise SystemExit("Missing API key. Pass --api-key, set OPENAI_API_KEY, or write it to .env.")

    skill_root = Path(__file__).resolve().parents[1]
    system_prompt = (skill_root / "references" / "metadata_extraction_system.md").read_text(encoding="utf-8")
    classification_labels = (
        load_classification_rules(Path(args.classification_rules).resolve())
        if args.classification_rules
        else {}
    )
    meta_dir = review_root / "review-library" / "metadata" / "papers"
    out_dir = review_root / "review-library" / "metadata"
    paths = selected_paths(meta_dir, args.paper_id)
    target_paths = pending_paths(paths, args.force)
    total_target = len(target_paths)
    print(f"Target papers: {total_target}; batch_size={args.batch_size}; model={model}; base_url={base_url}")
    if not target_paths:
        write_progress(out_dir, [], {}, 0)
        print("No pending papers.")
        return 0

    attempts: dict[str, int] = {paper_id_from_path(path): 0 for path in target_paths}
    reports: list[dict[str, Any]] = []
    completed: set[str] = set()

    while len(completed) < total_target:
        active = [
            path
            for path in target_paths
            if paper_id_from_path(path) not in completed
            and (args.retry_forever or attempts[paper_id_from_path(path)] < args.max_attempts)
        ][: args.batch_size]
        if not active:
            remaining = [paper_id_from_path(path) for path in target_paths if paper_id_from_path(path) not in completed]
            print(f"Stopped with {len(remaining)} unfinished papers after max attempts: {', '.join(remaining[:20])}")
            write_progress(out_dir, reports, attempts, total_target)
            return 1

        print(f"Starting batch: {', '.join(paper_id_from_path(path) for path in active)}")
        batch_had_failure = False
        for path in active:
            pid = paper_id_from_path(path)
            attempts[pid] += 1
            try:
                report = retag_one(
                    path,
                    system_prompt,
                    api_key,
                    base_url,
                    model,
                    args.timeout,
                    reasoning_effort,
                    classification_labels,
                )
                report["attempt"] = attempts[pid]
                reports.append(report)
                completed.add(pid)
                print(f"{pid} ok attempt={attempts[pid]}")
            except Exception as exc:
                batch_had_failure = True
                report = {
                    "paper_id": pid,
                    "metadata_path": str(path),
                    "status": "failed",
                    "attempt": attempts[pid],
                    "error": format_error(exc),
                }
                reports.append(report)
                print(f"{pid} failed attempt={attempts[pid]}: {report['error']}")
            write_progress(out_dir, reports, attempts, total_target)
            if args.sleep_seconds:
                time.sleep(args.sleep_seconds)

        remaining_count = total_target - len(completed)
        print(f"Progress: {len(completed)}/{total_target} ok; remaining={remaining_count}")
        if batch_had_failure and remaining_count:
            time.sleep(args.retry_delay)

    write_progress(out_dir, reports, attempts, total_target)
    print(f"Completed {len(completed)}/{total_target} papers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
