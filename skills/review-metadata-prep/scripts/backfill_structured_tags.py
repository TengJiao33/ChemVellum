#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from prepare_metadata import (
    STRUCTURED_TAG_KEYS,
    apply_structured_tags_to_compat_fields,
    read_json,
    scored,
    update_quality,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill empty eight-category structured_tags into existing metadata files.")
    parser.add_argument("--review-root", default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing structured_tags.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    meta_dir = Path(args.review_root).resolve() / "review-library" / "metadata" / "papers"
    count = 0
    for path in sorted(meta_dir.glob("*.metadata.json")):
        meta = read_json(path)
        if meta.get("structured_tags") and not args.overwrite:
            continue
        meta["structured_tags"] = scored(
            {key: "not specified" for key in STRUCTURED_TAG_KEYS},
            "backfill_empty_structured_tags",
            0.0,
        )
        apply_structured_tags_to_compat_fields(meta)
        meta.setdefault("extraction", {}).setdefault("notes", [])
        meta["extraction"]["notes"].append("structured_tags_backfilled_empty")
        update_quality(meta)
        write_json(path, meta)
        count += 1
    print(f"Backfilled {count} metadata files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
