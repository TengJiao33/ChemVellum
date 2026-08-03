#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any


STABLE_CITATION_RE = re.compile(
    r"\[((?:@P\d{3,})(?:\s*[;,]\s*@P\d{3,})*)\]"
)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
GENERATED_REFERENCES_RE = re.compile(
    r"\n##\s+References\s*\n<!-- generated-references:start -->.*?"
    r"<!-- generated-references:end -->\s*$",
    re.S | re.I,
)
TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$",
    re.M,
)
WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'’-]*\b")
PLACEHOLDER_AUTHOR_RE = re.compile(
    r"^(?:a\.?\s+author|unknown|author information unavailable)$", re.I
)
AFFILIATION_AUTHOR_RE = re.compile(
    r"\b(?:department|division|university|institute|laboratory|school|faculty|engineering)\b",
    re.I,
)
TITLE_LIKE_AUTHOR_RE = re.compile(
    r"\b(?:synthesis|cataly[sz]ed|catalytic|coupling|reaction|mechanis[mt]|"
    r"chiral|axial|substitut|"
    r"spectroscop|characteri[sz]ation|electrochemical|photochemical|"
    r"polymer|nanoparticle|degradation|adsorption|computational|analysis)\w*\b",
    re.I,
)
RAW_LATEX_RE = re.compile(
    r"\\(?:ce|mathsf|mathrm|text|mathbf|operatorname)\s*\{"
)
ABSTRACT_HEADING_RE = re.compile(
    r"^\s{0,3}(?:#{1,6}\s+|\*\*)Abstract\b",
    re.I | re.M,
)
LEGACY_TILDE_SCRIPT_RE = re.compile(
    r"(?<=[A-Za-z0-9)\]])~[^~\s]{1,24}~"
)
TRAILING_FOOTNOTE_MARKER_RE = re.compile(r"\s*[†‡§¶#]+\s*$")
COPYRIGHT_AUTHOR_RE = re.compile(
    r"(?:©|\bcopyright\b|\bthe author\(s\)\b|\ball rights reserved\b|"
    r"\bcreative commons\b)",
    re.I,
)
INLINE_LETTER_HYPHEN_RE = re.compile(r"\b([NOSP])\s+-\s*(?=[a-z])")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def stable_ids(text: str) -> list[str]:
    paper_ids: list[str] = []
    for match in STABLE_CITATION_RE.finditer(text):
        for paper_id in re.findall(r"P\d{3,}", match.group(1)):
            if paper_id not in paper_ids:
                paper_ids.append(paper_id)
    return paper_ids


def projected_stable_ids(manuscript: Path) -> tuple[list[str], Path | None]:
    """Recover stable IDs for a numbered deliverable from its projection file."""
    projection_path = manuscript.parent / "citations.json"
    if not projection_path.is_file():
        return [], None
    try:
        projection = read_json(projection_path)
    except (OSError, json.JSONDecodeError):
        return [], None
    if not isinstance(projection, dict):
        return [], None
    projected_output = str(projection.get("output") or "").strip()
    if projected_output:
        output_path = Path(projected_output)
        if not output_path.is_absolute():
            output_path = projection_path.parent / output_path
        if output_path.resolve() != manuscript.resolve():
            return [], None
    mapping = projection.get("paper_to_number")
    if not isinstance(mapping, dict):
        return [], None
    ordered = sorted(
        (
            (str(paper_id), number)
            for paper_id, number in mapping.items()
            if re.fullmatch(r"P\d{3,}", str(paper_id))
            and isinstance(number, int)
        ),
        key=lambda item: item[1],
    )
    return [paper_id for paper_id, _number in ordered], projection_path


def markdown_image_paths(text: str) -> list[str]:
    """Return image destinations while allowing brackets inside alt text."""
    paths: list[str] = []
    cursor = 0
    while True:
        marker = text.find("![", cursor)
        if marker < 0:
            break
        index = marker + 2
        destination_start = -1
        while index < len(text):
            if text[index] == "\\":
                index += 2
                continue
            if text[index] == "]" and index + 1 < len(text) and text[index + 1] == "(":
                destination_start = index + 2
                break
            index += 1
        if destination_start < 0:
            cursor = marker + 2
            continue
        index = destination_start
        depth = 1
        while index < len(text):
            if text[index] == "\\":
                index += 2
                continue
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
                if depth == 0:
                    paths.append(text[destination_start:index])
                    cursor = index + 1
                    break
            index += 1
        else:
            cursor = marker + 2
    return paths


def resolve_local_path(review_root: Path, raw: Any) -> Path | None:
    value = str(raw or "").strip()
    if not value:
        return None
    path = Path(value)
    candidate = path if path.is_absolute() else review_root / path
    candidate = candidate.resolve()
    return candidate if candidate.is_file() else None


def authors_are_placeholder(value: Any) -> bool:
    authors = unwrap(value)
    if isinstance(authors, dict):
        authors = [authors]
    if isinstance(authors, str):
        authors = [authors]
    if not isinstance(authors, list) or not authors:
        return True
    rendered: list[str] = []
    for author in authors:
        if isinstance(author, str):
            rendered.append(author.strip())
        elif isinstance(author, dict):
            name = str(
                author.get("name")
                or author.get("literal")
                or " ".join(
                    part
                    for part in (
                        str(author.get("given") or "").strip(),
                        str(author.get("family") or "").strip(),
                    )
                    if part
                )
            ).strip()
            if name:
                rendered.append(name)
    return not rendered or all(PLACEHOLDER_AUTHOR_RE.match(name) for name in rendered)


def rendered_author_names(value: Any) -> list[str]:
    authors = unwrap(value)
    if isinstance(authors, (str, dict)):
        authors = [authors]
    if not isinstance(authors, list):
        return []
    names: list[str] = []
    for author in authors:
        if isinstance(author, str):
            name = author.strip()
        elif isinstance(author, dict):
            name = str(
                author.get("name")
                or author.get("literal")
                or author.get("display_name")
                or " ".join(
                    part
                    for part in (
                        str(author.get("given") or "").strip(),
                        str(author.get("family") or "").strip(),
                    )
                    if part
                )
            ).strip()
        else:
            name = ""
        if name:
            names.append(name)
    return names


def recommended_range(profile: str, usable_sources: int) -> dict[str, int | str]:
    if profile == "focused":
        center = max(2500, min(8000, 1200 + 160 * usable_sources))
    else:
        center = max(4000, min(12000, 1500 + 200 * usable_sources))
    return {
        "basis": "cited_papers_with_local_full_text",
        "usable_source_count": usable_sources,
        "center": center,
        "low": int(math.floor(center * 0.8 / 100.0) * 100),
        "high": int(math.ceil(center * 1.2 / 100.0) * 100),
    }


def inspect(
    review_root: Path,
    manuscript: Path,
    profile: str,
    *,
    include_word_advisory: bool = False,
) -> dict[str, Any]:
    text = manuscript.read_text(encoding="utf-8")
    body = GENERATED_REFERENCES_RE.sub("", text)
    visible_body = COMMENT_RE.sub("", body)
    paper_ids = stable_ids(body)
    citation_provenance = "stable_paper_ids"
    projection_path: Path | None = None
    if not paper_ids:
        paper_ids, projection_path = projected_stable_ids(manuscript)
        if paper_ids:
            citation_provenance = "sibling_citation_projection"
    metadata_dir = review_root / "review-library" / "metadata" / "papers"

    citations: list[dict[str, Any]] = []
    unknown_ids: list[str] = []
    full_text_ids: list[str] = []
    metadata_warnings: list[str] = []
    for paper_id in paper_ids:
        metadata_path = metadata_dir / f"{paper_id}.metadata.json"
        if not metadata_path.is_file():
            unknown_ids.append(paper_id)
            citations.append(
                {
                    "paper_id": paper_id,
                    "metadata_found": False,
                    "local_full_text": False,
                }
            )
            continue
        metadata = read_json(metadata_path)
        paths = metadata.get("source_paths") if isinstance(metadata, dict) else {}
        paths = paths if isinstance(paths, dict) else {}
        full_text_path = (
            resolve_local_path(review_root, paths.get("markdown"))
            or resolve_local_path(review_root, paths.get("xml"))
            or resolve_local_path(review_root, paths.get("pdf"))
        )
        if full_text_path:
            full_text_ids.append(paper_id)
        if authors_are_placeholder(metadata.get("authors")):
            metadata_warnings.append(f"{paper_id}: missing or placeholder authors")
        names = rendered_author_names(metadata.get("authors"))
        if any(AFFILIATION_AUTHOR_RE.search(name) for name in names):
            metadata_warnings.append(f"{paper_id}: author field includes affiliation text")
        if any(TITLE_LIKE_AUTHOR_RE.search(name) for name in names):
            metadata_warnings.append(f"{paper_id}: author field looks title-like")
        if any(COPYRIGHT_AUTHOR_RE.search(name) for name in names):
            metadata_warnings.append(
                f"{paper_id}: author field includes a copyright statement"
            )
        for field in ("title", "year"):
            if not unwrap(metadata.get(field)):
                metadata_warnings.append(f"{paper_id}: missing {field}")
        title = str(unwrap(metadata.get("title")) or "")
        if RAW_LATEX_RE.search(title):
            metadata_warnings.append(f"{paper_id}: title contains raw LaTeX")
        if TRAILING_FOOTNOTE_MARKER_RE.search(title):
            metadata_warnings.append(
                f"{paper_id}: title has a trailing footnote marker"
            )
        if INLINE_LETTER_HYPHEN_RE.search(title):
            metadata_warnings.append(
                f"{paper_id}: title has inline-markup spacing around a hyphen"
            )
        citations.append(
            {
                "paper_id": paper_id,
                "metadata_found": True,
                "local_full_text": bool(full_text_path),
                "source_path": str(full_text_path) if full_text_path else None,
            }
        )

    image_rows: list[dict[str, Any]] = []
    missing_images: list[str] = []
    for raw in markdown_image_paths(body):
        path_text = raw.strip().strip("<>")
        if re.match(r"^(?:https?://|data:)", path_text, re.I):
            image_rows.append({"path": path_text, "kind": "remote", "exists": True})
            continue
        image_path = (manuscript.parent / path_text).resolve()
        exists = image_path.is_file()
        image_rows.append(
            {"path": path_text, "kind": "local", "exists": exists}
        )
        if not exists:
            missing_images.append(path_text)

    words = len(WORD_RE.findall(visible_body))
    errors = [f"unknown stable citation: {paper_id}" for paper_id in unknown_ids]
    errors.extend(f"missing local image: {path}" for path in missing_images)
    observations: list[str] = []
    if paper_ids and len(full_text_ids) < len(paper_ids):
        observations.append(
            f"{len(paper_ids) - len(full_text_ids)} cited papers lack a resolvable local full-text path"
        )
    if not paper_ids:
        observations.append("no stable paper-ID citations were found")
    table_count = len(TABLE_SEPARATOR_RE.findall(body))
    if table_count == 0:
        observations.append("no Markdown tables were found")
    if not image_rows:
        observations.append("no Markdown images were found")
    if profile == "comprehensive" and not ABSTRACT_HEADING_RE.search(visible_body):
        observations.append("no Abstract heading was found")
    if LEGACY_TILDE_SCRIPT_RE.search(visible_body):
        observations.append(
            "legacy ~...~ chemistry markup was found; use _..._, ^...^, or $\\ce{...}$"
        )

    report = {
        "report_type": "advisory_snapshot",
        "manuscript": str(manuscript.resolve()),
        "profile": profile,
        "substantive_word_count": words,
        "citation_count": len(paper_ids),
        "citation_provenance": citation_provenance,
        "citation_projection": str(projection_path) if projection_path else None,
        "cited_paper_ids": paper_ids,
        "cited_with_local_full_text": full_text_ids,
        "citations": citations,
        "table_count": table_count,
        "image_count": len(image_rows),
        "images": image_rows,
        "mechanical_errors": errors,
        "metadata_observations": sorted(set(metadata_warnings)),
        "editorial_observations": observations,
        "interpretation": (
            "Counts are descriptive writing aids. This report is not a quality "
            "verdict, release state, semantic review, or editorial target."
        ),
    }
    if include_word_advisory:
        report["word_advisory"] = recommended_range(profile, len(full_text_ids))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect a review manuscript without creating a completion gate."
    )
    parser.add_argument(
        "--review-root", default=str(Path(__file__).resolve().parents[3])
    )
    parser.add_argument("--input", required=True, help="Canonical Markdown manuscript")
    parser.add_argument(
        "--profile", choices=("focused", "comprehensive"), default="comprehensive"
    )
    parser.add_argument(
        "--include-word-advisory",
        action="store_true",
        help=(
            "Include a source-count-based length reference after substantive "
            "revision. It is omitted by default so it does not become a drafting target."
        ),
    )
    parser.add_argument("--output", help="Optional JSON output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    review_root = Path(args.review_root).resolve()
    manuscript = Path(args.input).resolve()
    if not manuscript.is_file():
        raise SystemExit(f"Manuscript not found: {manuscript}")
    report = inspect(
        review_root,
        manuscript,
        args.profile,
        include_word_advisory=args.include_word_advisory,
    )
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["mechanical_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
