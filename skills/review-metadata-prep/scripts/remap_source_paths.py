#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def portable_path(review_root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve()
    try:
        return resolved.relative_to(review_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def extract_archive(archive_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    target_root = target_dir.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            destination = (target_dir / member.filename).resolve()
            if destination != target_root and target_root not in destination.parents:
                raise ValueError(f"Archive entry escapes target directory: {archive_path.name}:{member.filename}")
        archive.extractall(target_dir)


def archive_fully_extracted(archive_path: Path, target_dir: Path) -> bool:
    if not archive_path.exists() or not target_dir.exists():
        return False
    with zipfile.ZipFile(archive_path) as archive:
        return all(
            member.is_dir() or (target_dir / member.filename).is_file()
            for member in archive.infolist()
        )


def choose_extracted_dir(review_root: Path, slug: str, paper_id: str) -> Path:
    base = review_root / "mineru-outputs" / "extracted"
    slug_dir = base / slug
    paper_dir = base / paper_id
    if list(paper_dir.rglob("*_content_list.json")):
        return paper_dir
    if list(slug_dir.rglob("*_content_list.json")):
        return slug_dir
    return paper_dir


def content_list_target(review_root: Path, slug: str, extracted_dir: Path) -> Path | None:
    candidates = sorted(extracted_dir.rglob("*_content_list.json"))
    if candidates:
        return candidates[0].resolve()

    archive_path = review_root / "mineru-outputs" / "raw_zips" / f"{slug}.zip"
    if not archive_path.exists():
        return None
    with zipfile.ZipFile(archive_path) as archive:
        names = sorted(
            name for name in archive.namelist()
            if name.lower().endswith("_content_list.json") and not name.endswith("/")
        )
    if not names:
        return None
    return (extracted_dir / Path(names[0])).resolve()


def remap_metadata(
    review_root: Path,
    metadata_path: Path,
    source_index: dict[str, Path] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = read_json(metadata_path)
    paper_id = str(metadata.get("paper_id") or metadata_path.stem.split(".", 1)[0])
    slug = str(metadata.get("slug") or "")
    source_file = metadata.get("source_file") or {}
    old_paths = metadata.get("source_paths") or {}
    pdf_name = str(source_file.get("pdf_name") or "") or Path(
        str(old_paths.get("pdf") or "")
    ).name
    xml_name = Path(str(old_paths.get("xml") or "")).name
    old_markdown = Path(str(old_paths.get("markdown") or ""))
    markdown_name = old_markdown.name or f"{slug}.md"

    indexed = source_index or {}
    pdf_path = indexed.get(pdf_name) if pdf_name else None
    xml_path = indexed.get(xml_name) if xml_name else None
    markdown_path = (review_root / "mineru-outputs" / "markdown" / markdown_name).resolve()
    extracted_dir = choose_extracted_dir(review_root, slug, paper_id).resolve()
    content_path = content_list_target(review_root, slug, extracted_dir)

    metadata["source_paths"] = {
        "pdf": portable_path(review_root, pdf_path) if pdf_path and pdf_path.exists() else None,
        "xml": portable_path(review_root, xml_path) if xml_path and xml_path.exists() else None,
        "markdown": portable_path(review_root, markdown_path) if markdown_path.exists() else None,
        "content_list": portable_path(review_root, content_path),
        "extracted_dir": portable_path(review_root, extracted_dir),
    }
    extraction = metadata.get("extraction")
    if isinstance(extraction, dict):
        inputs = extraction.get("inputs")
        if isinstance(inputs, dict) and "manifest" in inputs:
            inputs["manifest"] = portable_path(
                review_root, review_root / "mineru-outputs" / "manifest.json"
            )

    registry_update = {
        "paper_id": paper_id,
        "source_pdf": metadata["source_paths"]["pdf"],
        "markdown_path": metadata["source_paths"]["markdown"],
        "content_list_path": metadata["source_paths"]["content_list"],
        "metadata_path": portable_path(review_root, metadata_path),
    }
    return metadata, registry_update


def run(review_root: Path, write: bool, extract_archives: bool) -> int:
    metadata_dir = review_root / "review-library" / "metadata" / "papers"
    registry_path = review_root / "review-library" / "registry" / "papers.jsonl"
    updates: dict[str, dict[str, Any]] = {}
    mapped = 0
    source_ready = 0
    content_ready = 0
    extracted = 0
    source_index: dict[str, Path] = {}
    for source_path in sorted((review_root / "chem_papers").rglob("*")):
        if source_path.is_file():
            source_index.setdefault(source_path.name, source_path.resolve())

    for metadata_path in sorted(metadata_dir.glob("*.json")):
        if extract_archives:
            current = read_json(metadata_path)
            slug = str(current.get("slug") or "")
            paper_id = str(current.get("paper_id") or metadata_path.stem.split(".", 1)[0])
            target_dir = choose_extracted_dir(review_root, slug, paper_id)
            archive_path = review_root / "mineru-outputs" / "raw_zips" / f"{slug}.zip"
            if archive_path.exists() and not archive_fully_extracted(archive_path, target_dir):
                target_dir = review_root / "mineru-outputs" / "extracted" / paper_id
                if not archive_fully_extracted(archive_path, target_dir):
                    extract_archive(archive_path, target_dir)
                    extracted += 1
        metadata, registry_update = remap_metadata(
            review_root, metadata_path, source_index
        )
        updates[registry_update["paper_id"]] = registry_update
        mapped += 1
        source_ready += bool(
            metadata["source_paths"].get("pdf")
            or metadata["source_paths"].get("xml")
        )
        content_ready += bool(metadata["source_paths"]["content_list"])
        if write:
            write_json(metadata_path, metadata)

    if registry_path.exists():
        rows = [json.loads(line) for line in registry_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for row in rows:
            update = updates.get(str(row.get("paper_id") or ""))
            if update:
                row.update({key: value for key, value in update.items() if key != "paper_id"})
        if write:
            tmp = registry_path.with_suffix(registry_path.suffix + ".tmp")
            tmp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            tmp.replace(registry_path)

    mode = "updated" if write else "checked"
    print(f"Metadata paths {mode}: {mapped}")
    print(f"Source-document paths mapped: {source_ready}")
    print(f"Content-list paths mapped: {content_ready}")
    print(f"Archives extracted: {extracted}")
    print(f"Review root: {review_root}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remap metadata and registry paths to the current ChemVellum root.")
    parser.add_argument("--review-root", default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--extract-archives", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(run(Path(args.review_root).resolve(), args.write, args.extract_archives))
