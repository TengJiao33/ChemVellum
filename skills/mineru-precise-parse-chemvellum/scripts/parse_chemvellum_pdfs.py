#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

import requests


MINERU_BASE_URL = "https://mineru.net"
SKILL_ROOT = Path(__file__).resolve().parents[1]


def default_review_root() -> Path:
    if SKILL_ROOT.parent.name == "skills":
        return SKILL_ROOT.parent.parent
    return SKILL_ROOT.parent


REVIEW_ROOT = default_review_root()
DEFAULT_INPUT_DIR = REVIEW_ROOT / "chem_papers"
DEFAULT_OUTPUT_DIR = REVIEW_ROOT / "mineru-outputs"
DEFAULT_TOKEN_FILE = SKILL_ROOT / "config" / "mineru_api_token.txt"
DEFAULT_TIMEOUT_MINUTES = 30
DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_BATCH_SIZE = 20
SUCCESS_STATES = {"done", "success", "finished", "completed"}
FAILURE_STATES = {"failed", "error"}
TERMINAL_STATES = SUCCESS_STATES | FAILURE_STATES


@dataclass
class ParseJob:
    index: int
    pdf_path: Path
    source_root: Path
    slug: str
    data_id: str

    @property
    def file_name(self) -> str:
        return self.pdf_path.name

    @property
    def relative_pdf_path(self) -> str:
        return str(self.pdf_path.relative_to(self.source_root))


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    ensure_parent(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse local PDFs into Markdown via the MinerU precise parsing batch API."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"Directory containing local PDF files. Default: {DEFAULT_INPUT_DIR}",
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        action="append",
        default=[],
        help="Optional PDF path. Repeat --pdf to parse an explicit batch instead of scanning the input tree.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--token",
        help="MinerU API token. If omitted, MINERU_API_TOKEN and then config/mineru_api_token.txt are used.",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="MinerU language hint. Default: en",
    )
    parser.add_argument(
        "--model-version",
        default="vlm",
        help="MinerU model version. Default: vlm",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Number of PDFs per batch request. Default: {DEFAULT_BATCH_SIZE}",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional maximum number of PDFs to process. Default: 0 (all PDFs).",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help=f"Polling interval in seconds. Default: {DEFAULT_POLL_INTERVAL_SECONDS}",
    )
    parser.add_argument(
        "--timeout-minutes",
        type=int,
        default=DEFAULT_TIMEOUT_MINUTES,
        help=f"Maximum wait time per batch. Default: {DEFAULT_TIMEOUT_MINUTES}",
    )
    parser.add_argument(
        "--disable-formula",
        action="store_true",
        help="Disable MinerU formula parsing.",
    )
    parser.add_argument(
        "--disable-table",
        action="store_true",
        help="Disable MinerU table parsing.",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="Force OCR mode for uploaded PDFs.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess files even if Markdown already exists in the output directory.",
    )
    return parser.parse_args()


def resolve_token(args: argparse.Namespace) -> str:
    token = (args.token or "").strip()
    if token:
        return token
    token = os.environ.get("MINERU_API_TOKEN", "").strip()
    if token:
        return token
    if DEFAULT_TOKEN_FILE.is_file():
        token = DEFAULT_TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    raise SystemExit(
        "Missing MinerU API token. Pass --token, set MINERU_API_TOKEN, or write the token to "
        f"{DEFAULT_TOKEN_FILE}."
    )


def slugify_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._/-]+", "-", ascii_text).strip("-._/")
    cleaned = cleaned.replace("/", "__")
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned.lower() or "document"


def chunked(items: List[ParseJob], size: int) -> Iterable[List[ParseJob]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def should_skip_path(path: Path, input_dir: Path, output_dir: Path) -> bool:
    resolved = path.resolve()
    blocked_roots = [SKILL_ROOT.resolve(), output_dir.resolve()]
    for root in blocked_roots:
        if resolved == root or root in resolved.parents:
            return True
    return False


def discover_jobs(input_dir: Path, output_dir: Path, limit: int, force: bool) -> List[ParseJob]:
    pdfs = [
        path
        for path in sorted(input_dir.rglob("*.pdf"))
        if path.is_file() and not should_skip_path(path, input_dir, output_dir)
    ]
    if limit > 0:
        pdfs = pdfs[:limit]
    jobs: List[ParseJob] = []
    seen: Dict[str, int] = {}
    for index, pdf_path in enumerate(pdfs, start=1):
        relative_stem = str(pdf_path.relative_to(input_dir).with_suffix(""))
        base_slug = slugify_text(relative_stem)
        seen[base_slug] = seen.get(base_slug, 0) + 1
        slug = base_slug if seen[base_slug] == 1 else f"{base_slug}-{seen[base_slug]:02d}"
        data_id = f"{index:03d}-{slug}"[:96]
        markdown_path = output_dir / "markdown" / f"{slug}.md"
        if markdown_path.is_file() and not force:
            print(f"[skip] {pdf_path.relative_to(input_dir)} -> existing {markdown_path.name}")
            continue
        jobs.append(
            ParseJob(
                index=index,
                pdf_path=pdf_path,
                source_root=input_dir,
                slug=slug,
                data_id=data_id,
            )
        )
    return jobs


def discover_single_job(pdf_path: Path, input_dir: Path, output_dir: Path, force: bool) -> List[ParseJob]:
    resolved_pdf = pdf_path.resolve()
    if not resolved_pdf.is_file():
        raise SystemExit(f"PDF does not exist: {resolved_pdf}")
    if resolved_pdf.suffix.lower() != ".pdf":
        raise SystemExit(f"Path is not a PDF: {resolved_pdf}")

    if should_skip_path(resolved_pdf, input_dir, output_dir):
        raise SystemExit(f"Refusing to parse a blocked path: {resolved_pdf}")

    try:
        relative_stem = str(resolved_pdf.relative_to(input_dir).with_suffix(""))
    except ValueError:
        relative_stem = resolved_pdf.stem

    slug = slugify_text(relative_stem)
    markdown_path = output_dir / "markdown" / f"{slug}.md"
    if markdown_path.is_file() and not force:
        print(f"[skip] {resolved_pdf} -> existing {markdown_path.name}")
        return []

    return [
        ParseJob(
            index=1,
            pdf_path=resolved_pdf,
            source_root=input_dir,
            slug=slug,
            data_id=f"001-{slug}"[:96],
        )
    ]


def discover_selected_jobs(
    pdf_paths: List[Path],
    input_dir: Path,
    output_dir: Path,
    force: bool,
) -> List[ParseJob]:
    jobs: List[ParseJob] = []
    seen_paths: set[Path] = set()
    for pdf_path in pdf_paths:
        resolved = pdf_path.resolve()
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        selected = discover_single_job(resolved, input_dir, output_dir, force)
        if not selected:
            continue
        job = selected[0]
        job.index = len(jobs) + 1
        job.data_id = f"{job.index:03d}-{job.slug}"[:96]
        jobs.append(job)
    return jobs


def mineru_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def api_post_json(session: requests.Session, token: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = session.post(
        f"{MINERU_BASE_URL}{path}",
        headers=mineru_headers(token),
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"MinerU API error for {path}: {data.get('msg') or data}")
    return data


def api_get_json(session: requests.Session, token: str, path: str) -> Dict[str, Any]:
    response = session.get(
        f"{MINERU_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"MinerU API error for {path}: {data.get('msg') or data}")
    return data


def request_upload_batch(
    session: requests.Session,
    token: str,
    jobs: List[ParseJob],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    payload = {
        "enable_formula": not args.disable_formula,
        "enable_table": not args.disable_table,
        "language": args.language,
        "model_version": args.model_version,
        "files": [
            {
                "name": job.file_name,
                "is_ocr": args.ocr,
                "data_id": job.data_id,
            }
            for job in jobs
        ],
    }
    result = api_post_json(session, token, "/api/v4/file-urls/batch", payload)
    data = result.get("data") or {}
    upload_urls = data.get("file_urls") or []
    if len(upload_urls) != len(jobs):
        raise RuntimeError(
            f"MinerU returned {len(upload_urls)} upload URLs for {len(jobs)} jobs."
        )
    data["request_payload"] = payload
    return data


def upload_batch_files(jobs: List[ParseJob], upload_urls: List[str]) -> None:
    for job, upload_url in zip(jobs, upload_urls):
        with job.pdf_path.open("rb") as handle:
            response = requests.put(upload_url, data=handle, timeout=300)
        response.raise_for_status()
        print(f"[upload] {job.relative_pdf_path}")


def poll_batch_results(
    session: requests.Session,
    token: str,
    batch_id: str,
    jobs: List[ParseJob],
    poll_interval: int,
    timeout_minutes: int,
) -> Dict[str, Dict[str, Any]]:
    deadline = time.time() + timeout_minutes * 60
    last_states: Dict[str, str] = {}
    final_results: Dict[str, Dict[str, Any]] = {}
    tracked_ids = {job.data_id for job in jobs}

    while time.time() < deadline:
        payload = api_get_json(session, token, f"/api/v4/extract-results/batch/{batch_id}")
        items = ((payload.get("data") or {}).get("extract_result")) or []
        for item in items:
            data_id = item.get("data_id")
            if data_id not in tracked_ids:
                continue
            state = str(item.get("state") or "").strip()
            final_results[data_id] = item
            if last_states.get(data_id) != state:
                print(f"[poll] {data_id}: {state}")
                last_states[data_id] = state
        if len(final_results) == len(tracked_ids):
            active = [
                item for item in final_results.values()
                if str(item.get("state") or "").lower() not in TERMINAL_STATES
            ]
            if not active:
                return final_results
        time.sleep(max(1, poll_interval))
    raise TimeoutError(f"Timed out waiting for MinerU batch {batch_id}.")


def prepare_target(path: Path, force: bool) -> None:
    if path.exists() and force:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    if path.suffix:
        ensure_parent(path)
    else:
        path.mkdir(parents=True, exist_ok=True)


def download_binary(url: str, dest: Path) -> None:
    ensure_parent(dest)
    with requests.get(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        with dest.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def rewrite_image_paths(markdown: str, slug: str) -> str:
    text = markdown
    text = text.replace("(images/", f"(../extracted/{slug}/images/")
    text = text.replace('src="images/', f'src="../extracted/{slug}/images/')
    text = text.replace("src='images/", f"src='../extracted/{slug}/images/")
    return text


def materialize_markdown(output_dir: Path, job: ParseJob, zip_url: str, force: bool) -> Dict[str, Any]:
    raw_zip = output_dir / "raw_zips" / f"{job.slug}.zip"
    extracted_dir = output_dir / "extracted" / job.slug
    markdown_path = output_dir / "markdown" / f"{job.slug}.md"

    prepare_target(raw_zip, force)
    prepare_target(extracted_dir, force)

    download_binary(zip_url, raw_zip)
    with zipfile.ZipFile(raw_zip) as archive:
        archive.extractall(extracted_dir)

    full_md = extracted_dir / "full.md"
    if not full_md.is_file():
        candidates = sorted(extracted_dir.rglob("*.md"))
        if not candidates:
            raise RuntimeError(f"No Markdown file found in extracted result for {job.file_name}.")
        full_md = candidates[0]

    rewritten = rewrite_image_paths(full_md.read_text(encoding="utf-8"), job.slug)
    ensure_parent(markdown_path)
    markdown_path.write_text(rewritten, encoding="utf-8")

    return {
        "pdf_name": job.file_name,
        "relative_pdf_path": job.relative_pdf_path,
        "slug": job.slug,
        "data_id": job.data_id,
        "raw_zip": str(raw_zip),
        "extracted_dir": str(extracted_dir),
        "full_md": str(full_md),
        "markdown_copy": str(markdown_path),
    }


def summarize_batch_states(results: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in results.values():
        state = str(item.get("state") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    return counts


def run_batch(
    session: requests.Session,
    token: str,
    jobs: List[ParseJob],
    args: argparse.Namespace,
    output_dir: Path,
    manifest: Dict[str, Any],
) -> None:
    upload_batch = request_upload_batch(session, token, jobs, args)
    batch_id = str(upload_batch.get("batch_id") or "").strip()
    if not batch_id:
        raise RuntimeError("MinerU did not return a batch_id.")
    print(f"[batch] {batch_id} ({len(jobs)} files)")

    upload_batch_files(jobs, list(upload_batch.get("file_urls") or []))
    results = poll_batch_results(
        session=session,
        token=token,
        batch_id=batch_id,
        jobs=jobs,
        poll_interval=args.poll_interval,
        timeout_minutes=args.timeout_minutes,
    )

    batch_record = {
        "batch_id": batch_id,
        "created_at": now_utc(),
        "jobs": [],
        "state_counts": summarize_batch_states(results),
    }
    manifest["batches"].append(batch_record)

    for job in jobs:
        result = results.get(job.data_id) or {}
        state = str(result.get("state") or "unknown").lower()
        job_record: Dict[str, Any] = {
            "pdf_name": job.file_name,
            "relative_pdf_path": job.relative_pdf_path,
            "slug": job.slug,
            "data_id": job.data_id,
            "state": state,
            "err_msg": result.get("err_msg") or "",
        }
        if state in SUCCESS_STATES:
            zip_url = str(result.get("full_zip_url") or "").strip()
            if not zip_url:
                job_record["state"] = "failed"
                job_record["err_msg"] = "MinerU returned done without full_zip_url."
                manifest["failed"].append(job_record)
                batch_record["jobs"].append(job_record)
                continue
            output_record = materialize_markdown(output_dir, job, zip_url, force=args.force)
            job_record.update(output_record)
            manifest["completed"].append(job_record)
        else:
            manifest["failed"].append(job_record)
        batch_record["jobs"].append(job_record)


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    token = resolve_token(args)

    if not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    if args.pdf:
        jobs = discover_selected_jobs(args.pdf, input_dir, output_dir, args.force)
    else:
        jobs = discover_jobs(input_dir, output_dir, args.limit, args.force)
    if not jobs:
        print("No PDFs need processing.")
        return 0

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + f"-{os.getpid()}"
    manifest: Dict[str, Any] = {
        "tool": "mineru-precise-parse-chemvellum",
        "run_id": run_id,
        "skill_root": str(SKILL_ROOT),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "created_at": now_utc(),
        "settings": {
            "language": args.language,
            "model_version": args.model_version,
            "enable_formula": not args.disable_formula,
            "enable_table": not args.disable_table,
            "ocr": args.ocr,
            "batch_size": args.batch_size,
            "poll_interval": args.poll_interval,
            "timeout_minutes": args.timeout_minutes,
        },
        "queued": len(jobs),
        "batches": [],
        "completed": [],
        "failed": [],
    }

    session = requests.Session()
    try:
        for batch_jobs in chunked(jobs, max(1, args.batch_size)):
            run_batch(session, token, batch_jobs, args, output_dir, manifest)
    finally:
        session.close()

    manifest["finished_at"] = now_utc()
    manifest["completed_count"] = len(manifest["completed"])
    manifest["failed_count"] = len(manifest["failed"])
    run_manifest_path = output_dir / "runs" / f"{run_id}.json"
    write_json(run_manifest_path, manifest)
    write_json(output_dir / "manifest.json", manifest)

    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "completed_count": manifest["completed_count"],
                "failed_count": manifest["failed_count"],
                "manifest_path": str(output_dir / "manifest.json"),
                "run_manifest_path": str(run_manifest_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not manifest["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
