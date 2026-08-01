#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


LEGACY_FIELDS = [
    "keywords",
    "llm_tags",
    "human_tags",
    "topic_category",
    "reaction_category",
    "mechanism_category",
    "application_category",
]


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def clean_quality(meta: dict[str, Any]) -> None:
    quality = meta.get("quality")
    if not isinstance(quality, dict):
        return
    warnings = quality.get("warnings")
    if isinstance(warnings, list):
        quality["warnings"] = [
            warning
            for warning in warnings
            if warning not in {"empty_keywords", "empty_llm_tags", "missing_or_empty_keywords", "missing_or_empty_llm_tags"}
        ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove legacy tag fields from review metadata JSON files.")
    parser.add_argument("--review-root", default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    meta_dir = Path(args.review_root).resolve() / "review-library" / "metadata" / "papers"
    changed = 0
    removed = {field: 0 for field in LEGACY_FIELDS}
    for path in sorted(meta_dir.glob("*.metadata.json")):
        meta = read_json(path)
        if not isinstance(meta, dict):
            continue
        touched = False
        for field in LEGACY_FIELDS:
            if field in meta:
                removed[field] += 1
                meta.pop(field, None)
                touched = True
        clean_quality(meta)
        if touched:
            changed += 1
            if not args.dry_run:
                write_json(path, meta)
    print(f"changed_files={changed}")
    for field in LEGACY_FIELDS:
        print(f"{field}={removed[field]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
