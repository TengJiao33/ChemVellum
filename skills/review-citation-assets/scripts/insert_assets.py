#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any


ALLOWED_KINDS = {"figure", "scheme", "chart", "table"}
MARKER_TEMPLATE = "<!-- insert:{asset_id} -->"
PAPER_ID_CITATION_RE = re.compile(r"\[(?:@)?(P\d{3,})\]")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("assets")
    else:
        rows = None
    if not isinstance(rows, list):
        raise ValueError("Asset manifest must be a list or an object with an assets list")
    return [row for row in rows if isinstance(row, dict)]


def resolve_asset_path(manifest: Path, raw: Any) -> Path | None:
    value = str(raw or "").strip()
    if not value:
        return None
    path = Path(value)
    candidate = path if path.is_absolute() else manifest.parent / path
    candidate = candidate.resolve()
    return candidate if candidate.is_file() else None


def safe_name(asset_id: str, source: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", asset_id).strip("-") or "asset"
    return stem + source.suffix.lower()


def strip_repeated_label(label: str, caption: str) -> str:
    """Avoid captions such as ``Fig. 2. Fig. 2. ...``."""
    match = re.match(
        r"^(figure|fig\.?|scheme|chart|table)\s*([A-Za-z0-9.-]+)\s*$",
        label,
        re.I,
    )
    if not match:
        return caption
    kind, number = match.groups()
    kind_pattern = r"(?:figure|fig\.?)" if kind.lower().startswith("fig") else re.escape(kind)
    return re.sub(
        rf"^{kind_pattern}\s*{re.escape(number)}\s*[.:\-–—]?\s*",
        "",
        caption,
        count=1,
        flags=re.I,
    ).strip()


def normalize_paper_id_citations(text: str) -> str:
    return PAPER_ID_CITATION_RE.sub(r"[@\1]", text)


def validate_row(
    row: dict[str, Any], manifest: Path, seen_ids: set[str]
) -> tuple[Path | None, list[str]]:
    issues: list[str] = []
    asset_id = str(row.get("asset_id") or "").strip()
    if not asset_id:
        issues.append("asset_id is missing")
    elif asset_id in seen_ids:
        issues.append(f"duplicate asset_id: {asset_id}")
    else:
        seen_ids.add(asset_id)
    kind = str(row.get("kind") or "figure").strip().lower()
    if kind not in ALLOWED_KINDS:
        issues.append(f"{asset_id or '<unknown>'}: unsupported kind {kind!r}")
    source = resolve_asset_path(manifest, row.get("path"))
    if not source:
        issues.append(f"{asset_id or '<unknown>'}: asset path does not exist")
    if not str(row.get("caption") or "").strip():
        issues.append(f"{asset_id or '<unknown>'}: caption is missing")
    origin = str(row.get("origin") or "").strip().lower()
    if origin not in {"source_paper", "original"}:
        issues.append(
            f"{asset_id or '<unknown>'}: origin must be source_paper or original"
        )
    if origin == "source_paper":
        for field in (
            "source_paper_id",
            "source_locator",
            "reuse_basis",
            "attribution",
        ):
            if not str(row.get(field) or "").strip():
                issues.append(
                    f"{asset_id or '<unknown>'}: source-paper asset is missing {field}"
                )
    return source, issues


def insert_assets(
    input_path: Path,
    manifest_path: Path,
    output_path: Path,
    assets_dir: Path,
) -> dict[str, Any]:
    text = input_path.read_text(encoding="utf-8")
    rows = manifest_rows(read_json(manifest_path))
    seen_ids: set[str] = set()
    prepared: list[tuple[dict[str, Any], Path]] = []
    issues: list[str] = []
    for row in rows:
        source, row_issues = validate_row(row, manifest_path, seen_ids)
        issues.extend(row_issues)
        if source and not row_issues:
            prepared.append((row, source))

    for row, _source in prepared:
        asset_id = str(row["asset_id"]).strip()
        marker = str(row.get("insert_marker") or "").strip()
        if not marker:
            marker = MARKER_TEMPLATE.format(asset_id=asset_id)
        occurrences = text.count(marker)
        if occurrences != 1:
            issues.append(
                f"{asset_id}: insert marker must occur exactly once; found {occurrences}"
            )
    if issues:
        return {
            "report_type": "asset_insertion_report",
            "input": str(input_path),
            "manifest": str(manifest_path),
            "output": str(output_path),
            "inserted": [],
            "mechanical_errors": sorted(set(issues)),
            "note": "No manuscript or asset copies were written.",
        }

    assets_dir.mkdir(parents=True, exist_ok=True)
    inserted: list[dict[str, Any]] = []
    for row, source in prepared:
        asset_id = str(row["asset_id"]).strip()
        marker = str(row.get("insert_marker") or "").strip()
        if not marker:
            marker = MARKER_TEMPLATE.format(asset_id=asset_id)
        destination = assets_dir / safe_name(asset_id, source)
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        try:
            relative = destination.relative_to(output_path.parent).as_posix()
        except ValueError:
            relative = destination.as_posix()
        kind = str(row.get("kind") or "figure").strip().lower()
        label = str(row.get("label") or "").strip() or kind.title()
        caption = re.sub(r"\s+", " ", str(row.get("caption") or "")).strip()
        caption = strip_repeated_label(label, caption)
        attribution = re.sub(
            r"\s+", " ", str(row.get("attribution") or "")
        ).strip()
        caption = normalize_paper_id_citations(caption)
        attribution = normalize_paper_id_citations(attribution)
        alt = f"{label}. {caption}"
        if attribution:
            alt += f" {attribution}"
        alt = alt.replace("]", r"\]")
        block = f"![{alt}]({relative})"
        text = text.replace(marker, block, 1)
        inserted.append(
            {
                "asset_id": asset_id,
                "kind": kind,
                "label": label,
                "source_path": str(source),
                "copied_path": str(destination),
                "sha256": file_sha256(destination),
                "origin": row.get("origin"),
                "source_paper_id": row.get("source_paper_id"),
                "source_locator": row.get("source_locator"),
                "reuse_basis": row.get("reuse_basis"),
                "attribution": row.get("attribution"),
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(text.rstrip() + "\n", encoding="utf-8")
    temporary.replace(output_path)
    return {
        "report_type": "asset_insertion_report",
        "input": str(input_path),
        "manifest": str(manifest_path),
        "output": str(output_path),
        "inserted": inserted,
        "mechanical_errors": [],
        "note": (
            "The tool copied and inserted declared assets. It did not assess "
            "scientific fidelity, editorial value, or reuse legality."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Insert explicitly selected review assets at explicit markers."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--assets-dir",
        help="Destination for copied images. Defaults to an assets folder beside output.",
    )
    parser.add_argument("--report", help="Optional JSON report path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).resolve()
    manifest_path = Path(args.manifest).resolve()
    output_path = Path(args.output).resolve()
    if not input_path.is_file():
        raise SystemExit(f"Input manuscript not found: {input_path}")
    if not manifest_path.is_file():
        raise SystemExit(f"Asset manifest not found: {manifest_path}")
    assets_dir = (
        Path(args.assets_dir).resolve()
        if args.assets_dir
        else output_path.parent / "assets"
    )
    report = insert_assets(input_path, manifest_path, output_path, assets_dir)
    if args.report:
        report_path = Path(args.report).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["mechanical_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
