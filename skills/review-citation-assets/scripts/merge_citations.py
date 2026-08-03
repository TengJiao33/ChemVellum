#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path
from typing import Any


STABLE_CITATION_RE = re.compile(
    r"\[((?:@P\d{3,})(?:\s*[;,]\s*@P\d{3,})*)\\?\]"
)
GENERATED_REFERENCES_RE = re.compile(
    r"\n##\s+References\s*\n<!-- generated-references:start -->.*?"
    r"<!-- generated-references:end -->\s*$",
    re.S | re.I,
)
TRAILING_REFERENCES_HEADING_RE = re.compile(
    r"\n##\s+References\s*$",
    re.I,
)
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
TRAILING_FOOTNOTE_MARKER_RE = re.compile(r"\s*[†‡§¶#]+\s*$")
COPYRIGHT_AUTHOR_RE = re.compile(
    r"(?:©|\bcopyright\b|\bthe author\(s\)\b|\ball rights reserved\b|"
    r"\bcreative commons\b)",
    re.I,
)
INLINE_LETTER_HYPHEN_RE = re.compile(r"\b([NOSP])\s+-\s*(?=[a-z])")
HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
MARKDOWN_IMAGE_RE = re.compile(
    r"(!\[(?:\\.|[^\]])*\]\()([^)]+)(\))"
)
NON_LOCAL_IMAGE_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|#)")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def plain_reference_text(value: Any) -> str:
    text = html.unescape(str(unwrap(value) or ""))
    text = HTML_TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def stable_ids(text: str) -> list[str]:
    paper_ids: list[str] = []
    for match in STABLE_CITATION_RE.finditer(text):
        for paper_id in re.findall(r"P\d{3,}", match.group(1)):
            if paper_id not in paper_ids:
                paper_ids.append(paper_id)
    return paper_ids


def replace_stable_citations(text: str, order: list[str]) -> str:
    number_by_id = {paper_id: index + 1 for index, paper_id in enumerate(order)}

    def replace(match: re.Match[str]) -> str:
        numbers: list[int] = []
        for paper_id in re.findall(r"P\d{3,}", match.group(1)):
            number = number_by_id[paper_id]
            if number not in numbers:
                numbers.append(number)
        closing = r"\]" if match.group(0).endswith(r"\]") else "]"
        return "[" + ", ".join(str(number) for number in numbers) + closing

    return STABLE_CITATION_RE.sub(replace, text)


def rebase_markdown_image_paths(
    text: str, input_dir: Path, output_dir: Path
) -> str:
    """Keep local image links valid when the numbered copy moves directories."""

    def replace(match: re.Match[str]) -> str:
        raw_target = match.group(2).strip()
        wrapped = raw_target.startswith("<") and raw_target.endswith(">")
        target = raw_target[1:-1].strip() if wrapped else raw_target
        if not target or NON_LOCAL_IMAGE_RE.match(target) or Path(target).is_absolute():
            return match.group(0)
        source = (input_dir / target).resolve()
        try:
            rebased = Path(os.path.relpath(source, output_dir)).as_posix()
        except ValueError:
            rebased = source.as_posix()
        if wrapped:
            rebased = f"<{rebased}>"
        return match.group(1) + rebased + match.group(3)

    return MARKDOWN_IMAGE_RE.sub(replace, text)


def author_names(value: Any) -> tuple[list[str], bool]:
    authors = unwrap(value)
    if isinstance(authors, str):
        authors = [
            part.strip()
            for part in re.split(r";|\band\b", authors)
            if part.strip()
        ]
    elif isinstance(authors, dict):
        authors = [authors]
    if not isinstance(authors, list):
        return [], True
    names: list[str] = []
    for author in authors:
        if isinstance(author, str):
            name = author.strip()
        elif isinstance(author, dict):
            name = str(
                author.get("name")
                or author.get("literal")
                or author.get("display_name")
                or ""
            ).strip()
            if not name:
                given = str(
                    author.get("given")
                    or author.get("given_name")
                    or author.get("first")
                    or ""
                ).strip()
                family = str(
                    author.get("family")
                    or author.get("family_name")
                    or author.get("last")
                    or author.get("surname")
                    or ""
                ).strip()
                name = " ".join(part for part in (given, family) if part)
        else:
            name = ""
        if name:
            names.append(name)
    placeholder = not names or all(PLACEHOLDER_AUTHOR_RE.match(name) for name in names)
    return names, placeholder


def sentence_fragment(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "?", "!")) else f"{text}."


def format_reference(
    number: int, paper_id: str, metadata: dict[str, Any]
) -> tuple[str, list[str]]:
    issues: list[str] = []
    names, placeholder = author_names(metadata.get("authors"))
    if any(COPYRIGHT_AUTHOR_RE.search(name) for name in names):
        issues.append("authors_include_copyright_statement")
        names = [name for name in names if not COPYRIGHT_AUTHOR_RE.search(name)]
        placeholder = not names
    if placeholder:
        issues.append("authors_missing_or_placeholder")
        authors = "[authors unavailable]"
    elif len(names) > 6:
        authors = "; ".join(names[:3]) + "; et al."
    else:
        authors = "; ".join(names)
    if names and any(AFFILIATION_AUTHOR_RE.search(name) for name in names):
        issues.append("authors_include_affiliation_text")
    if names and any(TITLE_LIKE_AUTHOR_RE.search(name) for name in names):
        issues.append("authors_look_title_like")
    title = plain_reference_text(metadata.get("title"))
    if TRAILING_FOOTNOTE_MARKER_RE.search(title):
        issues.append("title_has_trailing_footnote_marker")
        title = TRAILING_FOOTNOTE_MARKER_RE.sub("", title).strip()
    if INLINE_LETTER_HYPHEN_RE.search(title):
        issues.append("title_has_inline_markup_spacing")
        title = INLINE_LETTER_HYPHEN_RE.sub(r"\1-", title)
    journal = plain_reference_text(metadata.get("journal")).rstrip(".")
    year = str(unwrap(metadata.get("year")) or "").strip()
    doi = str(unwrap(metadata.get("doi")) or "").strip()
    if not title:
        issues.append("title_missing")
        title = f"[title unavailable for {paper_id}]"
    elif RAW_LATEX_RE.search(title):
        issues.append("title_contains_raw_latex")
    if not journal:
        issues.append("journal_missing")
    if not year:
        issues.append("year_missing")
    pieces = [
        f"{number}. {sentence_fragment(authors)} {sentence_fragment(title)}"
    ]
    if journal:
        pieces.append(f"*{journal}*.")
    if year:
        pieces.append(f"{year}.")
    if doi:
        normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
        pieces.append(f"https://doi.org/{normalized}")
    elif not metadata.get("pages") and not metadata.get("article_number"):
        issues.append("doi_or_locator_missing")
    return " ".join(pieces), issues


def merge(
    review_root: Path,
    input_path: Path,
    output_path: Path,
    citations_path: Path,
    *,
    strict_metadata: bool,
) -> dict[str, Any]:
    original = input_path.read_text(encoding="utf-8")
    body = GENERATED_REFERENCES_RE.sub("", original).rstrip()
    body = TRAILING_REFERENCES_HEADING_RE.sub("", body).rstrip()
    order = stable_ids(body)
    metadata_dir = review_root / "review-library" / "metadata" / "papers"
    metadata_by_id: dict[str, dict[str, Any]] = {}
    unknown: list[str] = []
    for paper_id in order:
        path = metadata_dir / f"{paper_id}.metadata.json"
        if not path.is_file():
            unknown.append(paper_id)
            continue
        payload = read_json(path)
        metadata_by_id[paper_id] = payload if isinstance(payload, dict) else {}

    report: dict[str, Any] = {
        "report_type": "citation_projection",
        "input": str(input_path),
        "output": str(output_path),
        "numbering_rule": "first_appearance",
        "paper_to_number": {
            paper_id: index + 1 for index, paper_id in enumerate(order)
        },
        "unknown_paper_ids": unknown,
        "metadata_observations": [],
        "reference_count": len(order),
        "note": (
            "This file records deterministic citation numbering. It is not a "
            "semantic or bibliographic acceptance decision."
        ),
    }
    if unknown:
        citations_path.parent.mkdir(parents=True, exist_ok=True)
        citations_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return report

    references: list[str] = []
    observations: list[dict[str, Any]] = []
    for number, paper_id in enumerate(order, start=1):
        line, issues = format_reference(number, paper_id, metadata_by_id[paper_id])
        references.append(line)
        if issues:
            observations.append({"paper_id": paper_id, "issues": issues})
    report["metadata_observations"] = observations
    if strict_metadata and observations:
        citations_path.parent.mkdir(parents=True, exist_ok=True)
        citations_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return report

    numbered = replace_stable_citations(body, order)
    numbered = rebase_markdown_image_paths(
        numbered, input_path.parent, output_path.parent
    )
    if references:
        numbered += (
            "\n\n## References\n\n"
            "<!-- generated-references:start -->\n"
            + "\n".join(references)
            + "\n<!-- generated-references:end -->"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(numbered.rstrip() + "\n", encoding="utf-8")
    temporary.replace(output_path)
    citations_path.parent.mkdir(parents=True, exist_ok=True)
    citations_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Number stable paper-ID citations and build references."
    )
    parser.add_argument(
        "--review-root", default=str(Path(__file__).resolve().parents[3])
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--citations-json", required=True)
    parser.add_argument(
        "--strict-metadata",
        action="store_true",
        help="Do not write output Markdown when any cited record is incomplete.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    review_root = Path(args.review_root).resolve()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    citations_path = Path(args.citations_json).resolve()
    if not input_path.is_file():
        raise SystemExit(f"Input manuscript not found: {input_path}")
    report = merge(
        review_root,
        input_path,
        output_path,
        citations_path,
        strict_metadata=args.strict_metadata,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["unknown_paper_ids"]:
        return 2
    if args.strict_metadata and report["metadata_observations"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
