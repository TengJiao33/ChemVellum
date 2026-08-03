#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    readable = windows_io_path(path)
    if not readable.is_file():
        return ""
    digest = hashlib.sha256()
    with readable.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def needs_short_working_path(path: Path) -> bool:
    value = str(path)
    return os.name == "nt" and (
        value.startswith("\\\\?\\") or len(value) >= 240
    )


def windows_io_path(path: Path) -> Path:
    """Return an extended-length Windows path for local file operations."""
    resolved = str(path.resolve())
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return Path(resolved)
    if resolved.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + resolved[2:])
    return Path("\\\\?\\" + resolved)


def path_is_file(path: Path) -> bool:
    return windows_io_path(path).is_file()


def run_command(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def ps_literal(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def render_with_word(docx: Path, pdf: Path) -> tuple[bool, dict[str, Any]]:
    if os.name != "nt":
        return False, {"renderer": "Microsoft Word COM", "available": False}
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return False, {"renderer": "Microsoft Word COM", "available": False}
    pdf.parent.mkdir(parents=True, exist_ok=True)
    script = f"""
$ErrorActionPreference = 'Stop'
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {{
  $document = $word.Documents.Open({ps_literal(docx)}, $false, $true)
  try {{ $document.SaveAs2({ps_literal(pdf)}, 17) }} finally {{ $document.Close($false) }}
}} finally {{
  $word.Quit()
  [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) | Out-Null
}}
""".strip()
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = run_command([powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])
    public_command = [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", "<encoded Word export script>"]
    result["command"] = public_command
    return result["exit_code"] == 0 and pdf.exists(), {
        "renderer": "Microsoft Word COM",
        "available": True,
        **result,
    }


def find_pdftoppm() -> str | None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    candidates = []
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64" / "pdftoppm.exe")
    candidates.extend(
        [
            Path(sys.executable).resolve().parent.parent / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe",
            Path(r"C:\Program Files\poppler\Library\bin\pdftoppm.exe"),
            Path(r"C:\Program Files\MiKTeX\miktex\bin\x64\pdftoppm.exe"),
        ]
    )
    direct = next((path for path in candidates if path.exists()), None)
    if direct:
        return str(direct)
    return shutil.which("pdftoppm")


def pdf_page_count(pdf: Path) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf)).pages)
    except Exception:
        return None


def rasterize(pdf: Path, pages_dir: Path) -> tuple[list[str], dict[str, Any]]:
    executable = find_pdftoppm()
    if not executable:
        return [], {"available": False, "error": "pdftoppm not found"}
    pages_dir.mkdir(parents=True, exist_ok=True)
    for stale_page in pages_dir.glob("page-*.png"):
        stale_page.unlink()
    prefix = pages_dir / "page"
    result = run_command([executable, "-png", "-r", "150", str(pdf), str(prefix)])
    images = sorted(str(path) for path in pages_dir.glob("page-*.png"))
    return images, {"available": True, **result}


def layout_warnings(pdf: Path, page_images: list[str]) -> list[dict[str, Any]]:
    """Cheap warning signals; visual judgment requires opening the page images."""
    warnings: list[dict[str, Any]] = []
    try:
        from pypdf import PdfReader

        pages = PdfReader(str(pdf)).pages
        for index, page in enumerate(pages, start=1):
            text = re.sub(r"\s+", " ", page.extract_text() or "").strip()
            if 1 < index < len(pages) and len(text) < 120:
                warnings.append(
                    {"page": index, "signal": "very_little_extracted_text", "characters": len(text)}
                )
    except Exception:
        pass
    return warnings


def run(args: argparse.Namespace) -> int:
    docx = args.input.resolve()
    pdf = (
        args.output_pdf.resolve()
        if args.output_pdf is not None
        else docx.with_suffix(".pdf")
    )
    report_path = args.report.resolve()
    pages_dir = args.pages_dir.resolve()
    if not path_is_file(docx):
        path_note = (
            f" Path length is {len(str(docx))} characters; this may be a Windows "
            "long-path access problem."
            if os.name == "nt" and len(str(docx)) >= 240
            else ""
        )
        raise SystemExit(f"DOCX not found or inaccessible: {docx}.{path_note}")
    if pdf.suffix.lower() != ".pdf":
        raise SystemExit("--output-pdf must name a .pdf file")
    pdf_io = windows_io_path(pdf)
    if pdf_io.exists():
        pdf_io.unlink()
    temporary_workspace = None
    render_docx = docx
    render_pdf = pdf
    if needs_short_working_path(docx) or needs_short_working_path(pdf):
        temporary_workspace = tempfile.TemporaryDirectory(prefix="review-render-")
        working_dir = Path(temporary_workspace.name)
        render_docx = working_dir / "input.docx"
        render_pdf = working_dir / "output.pdf"
        shutil.copy2(windows_io_path(docx), render_docx)
    attempts = []
    rendered = False
    renderer = None
    for function in (render_with_word,):
        rendered, attempt = function(render_docx, render_pdf)
        if temporary_workspace is not None:
            attempt["used_short_working_path"] = True
        attempts.append(attempt)
        if rendered:
            renderer = attempt["renderer"]
            break
    if rendered and render_pdf != pdf:
        pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(render_pdf, windows_io_path(pdf))
    page_images: list[str] = []
    raster_report: dict[str, Any] = {"available": False, "error": "render failed"}
    if rendered:
        page_images, raster_report = rasterize(render_pdf, pages_dir)
    page_count = pdf_page_count(render_pdf) if rendered else None
    page_hashes = [
        {"page_number": index, "path": image_path, "sha256": file_sha256(Path(image_path))}
        for index, image_path in enumerate(page_images, start=1)
    ]
    mechanical_errors: list[str] = []
    if not rendered or not path_is_file(pdf):
        mechanical_errors.append("DOCX could not be rendered to PDF")
    if rendered and not page_images:
        mechanical_errors.append("PDF was created but page images could not be rasterized")
    report = {
        "report_type": "docx_render_report",
        "input_docx": str(docx),
        "output_pdf": str(pdf),
        "pages_dir": str(pages_dir),
        "created_at": utc_now(),
        "renderer": renderer,
        "input_docx_sha256": file_sha256(docx),
        "output_pdf_sha256": file_sha256(pdf),
        "pdf_created": rendered and path_is_file(pdf),
        "page_images_created": bool(page_images),
        "renderer_attempts": attempts,
        "rasterization": raster_report,
        "page_count": page_count,
        "page_images": page_images,
        "page_artifacts": page_hashes,
        "layout_warnings": (
            layout_warnings(render_pdf, page_images) if rendered else []
        ),
        "mechanical_errors": mechanical_errors,
        "note": (
            "This report records conversion and rasterization only. Open and "
            "inspect the actual page images; the script does not record a "
            "visual verdict or acceptance state."
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if temporary_workspace is not None:
        temporary_workspace.cleanup()
    return 0 if not mechanical_errors else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a review DOCX with Microsoft Word, then rasterize every page.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument(
        "--output-pdf",
        type=Path,
        help="Output PDF. Defaults to the DOCX path with a .pdf extension.",
    )
    parser.add_argument("--pages-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
