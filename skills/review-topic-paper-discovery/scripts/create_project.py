#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
REVIEW_ID_RE = re.compile(r"^CVR-(\d{4,})-")
EXPERIMENT_ID_RE = re.compile(r"^EXP-(\d{8})-(\d{3,})-")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


def synthesis_model_template(topic: str) -> str:
    """Seed one living explanation shared by reading, writing, and visuals."""
    return f"""# Synthesis Model

**Review question:** {topic}

## Current explanatory model

<!-- State the best current explanation of the reader question. Revise it when
full-text evidence changes the explanation, its conditions, or its boundaries. -->

## Relations that organize the review

<!-- Record the causal, conditional, comparative, or competing relationships
that change the answer. Organize by scientific relationship, not by paper. -->

## Evidence that changed the model

<!-- Note only evidence that materially established, distinguished, revised,
or bounded the explanation. Repetition need not become another entry. -->

## High-leverage uncertainties

<!-- Keep uncertainties whose resolution could change the central judgment,
the manuscript architecture, or an important scope boundary. -->

## Useful views

<!-- Note relationships best expressed as prose, a source visual, an original
high-level diagram, or an aligned comparison table. Do not set a count. -->
"""


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def slugify(value: str, limit: int = 72) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return (slug[:limit].rstrip("-") or "review")


def validate_identifier(value: str, label: str) -> str:
    identifier = str(value or "").strip()
    if not SAFE_ID_RE.fullmatch(identifier) or identifier in {".", ".."}:
        raise ValueError(
            f"{label} must contain only letters, digits, '.', '_' and '-', "
            "start with a letter or digit, and stay within 120 characters"
        )
    return identifier


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    temp.replace(path)


@contextmanager
def file_lock(path: Path, timeout: float = 10.0) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out waiting for workspace registry lock: {path}"
                    )
                time.sleep(0.05)
        yield
    finally:
        try:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def max_review_number(projects_root: Path, rows: list[dict[str, Any]]) -> int:
    values = [str(row.get("project_id") or "") for row in rows]
    if projects_root.exists():
        values.extend(path.name for path in projects_root.iterdir() if path.is_dir())
    numbers = [int(match.group(1)) for value in values if (match := REVIEW_ID_RE.match(value))]
    return max(numbers, default=0)


def max_experiment_number(
    experiments_root: Path,
    rows: list[dict[str, Any]],
    date_key: str,
) -> int:
    values = [str(row.get("experiment_id") or "") for row in rows]
    if experiments_root.exists():
        values.extend(path.name for path in experiments_root.iterdir() if path.is_dir())
    numbers = [
        int(match.group(2))
        for value in values
        if (match := EXPERIMENT_ID_RE.match(value)) and match.group(1) == date_key
    ]
    return max(numbers, default=0)


def ensure_review_project(
    review_root: Path,
    topic: str,
    *,
    title: str = "",
    project_id: str = "",
) -> dict[str, Any]:
    root = review_root.resolve()
    projects_root = root / "review-projects"
    registry = projects_root / "project_registry.jsonl"
    lock = projects_root / ".project_registry.lock"
    topic = str(topic or "").strip()
    title = str(title or "").strip()
    if not topic:
        raise ValueError("topic is required")

    with file_lock(lock):
        rows = read_jsonl(registry)
        if project_id:
            identifier = validate_identifier(project_id, "project_id")
        else:
            number = max_review_number(projects_root, rows) + 1
            identifier = f"CVR-{number:04d}-{slugify(title or topic)}"
        project = projects_root / identifier
        manifest_path = project / "project.json"
        existing = read_json(manifest_path)
        created_at = str(existing.get("created_at") or utc_now())
        manifest: dict[str, Any] = {
            **existing,
            "schema_version": 1,
            "kind": "review",
            "project_id": identifier,
            "topic": str(existing.get("topic") or topic),
            "title": str(existing.get("title") or title or topic),
            "created_at": created_at,
            "updated_at": utc_now(),
            "paths": {
                "manuscript": "manuscript.md",
                "synthesis_model": "notes/synthesis_model.md",
                "discovery": "00_discovery",
                "assets": "assets",
                "deliverables": "deliverables",
                "notes": "notes",
                "runs": "runs",
            },
        }
        project.mkdir(parents=True, exist_ok=True)
        for name in ("00_discovery", "assets", "deliverables", "notes", "runs"):
            (project / name).mkdir(exist_ok=True)
        manuscript = project / "manuscript.md"
        if not manuscript.exists():
            manuscript.write_text(f"# {manifest['title']}\n", encoding="utf-8")
        synthesis_model = project / "notes" / "synthesis_model.md"
        if not synthesis_model.exists():
            synthesis_model.write_text(
                synthesis_model_template(str(manifest["topic"])),
                encoding="utf-8",
            )
        write_json(manifest_path, manifest)

        registry_row = {
            "kind": "review",
            "project_id": identifier,
            "topic": manifest["topic"],
            "title": manifest["title"],
            "created_at": created_at,
            "path": f"review-projects/{identifier}",
        }
        found = False
        updated_rows: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("project_id") or "") == identifier:
                updated_rows.append(registry_row)
                found = True
            else:
                updated_rows.append(row)
        if not found:
            updated_rows.append(registry_row)
        write_jsonl(registry, updated_rows)
    return manifest


def ensure_experiment(
    review_root: Path,
    topic: str,
    *,
    experiment_id: str = "",
    date_key: str = "",
) -> dict[str, Any]:
    root = review_root.resolve()
    experiments_root = root / "workspace" / "experiments"
    registry = experiments_root / "experiment_registry.jsonl"
    lock = experiments_root / ".experiment_registry.lock"
    topic = str(topic or "").strip()
    if not topic:
        raise ValueError("topic is required")
    date_key = date_key or datetime.now(timezone.utc).strftime("%Y%m%d")
    if not re.fullmatch(r"\d{8}", date_key):
        raise ValueError("date_key must use YYYYMMDD")

    with file_lock(lock):
        rows = read_jsonl(registry)
        if experiment_id:
            identifier = validate_identifier(experiment_id, "experiment_id")
        else:
            number = max_experiment_number(experiments_root, rows, date_key) + 1
            identifier = f"EXP-{date_key}-{number:03d}-{slugify(topic, 60)}"
        experiment = experiments_root / identifier
        manifest_path = experiment / "experiment.json"
        existing = read_json(manifest_path)
        created_at = str(existing.get("created_at") or utc_now())
        manifest = {
            **existing,
            "schema_version": 1,
            "kind": "experiment",
            "experiment_id": identifier,
            "topic": str(existing.get("topic") or topic),
            "created_at": created_at,
            "updated_at": utc_now(),
            "paths": {"runs": "runs"},
        }
        (experiment / "runs").mkdir(parents=True, exist_ok=True)
        write_json(manifest_path, manifest)
        registry_row = {
            "kind": "experiment",
            "experiment_id": identifier,
            "topic": manifest["topic"],
            "created_at": created_at,
            "path": f"workspace/experiments/{identifier}",
        }
        found = False
        updated_rows = []
        for row in rows:
            if str(row.get("experiment_id") or "") == identifier:
                updated_rows.append(registry_row)
                found = True
            else:
                updated_rows.append(row)
        if not found:
            updated_rows.append(registry_row)
        write_jsonl(registry, updated_rows)
    return manifest


def archive_current_discovery(project: Path) -> Path | None:
    discovery = project / "00_discovery"
    if not discovery.is_dir() or not any(discovery.iterdir()):
        return None
    manifest_path = project / "project.json"
    manifest = read_json(manifest_path)
    run_id = str(manifest.get("current_discovery_run_id") or "").strip()
    if not run_id:
        contract = read_json(discovery / "topic_contract.json")
        run_id = str(contract.get("discovery_run_id") or "").strip()
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError(
            "Existing discovery output has no safe discovery_run_id; "
            "move it aside or repair project.json before rerunning"
        )
    target = project / "runs" / run_id / "discovery"
    if target.exists():
        raise FileExistsError(
            f"Discovery archive already exists for run {run_id}: {target}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    discovery.replace(target)
    discovery.mkdir()
    return target


def record_discovery_run(
    project: Path,
    run_id: str,
    topic: str,
    status: str,
) -> dict[str, Any]:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"Unsafe discovery_run_id: {run_id!r}")
    manifest_path = project / "project.json"
    manifest = read_json(manifest_path)
    runs = [row for row in manifest.get("discovery_runs", []) if isinstance(row, dict)]
    now = utc_now()
    if status == "in_progress":
        for previous in runs:
            if (
                str(previous.get("discovery_run_id") or "") != run_id
                and previous.get("status") == "in_progress"
            ):
                previous["status"] = "interrupted"
                previous["finished_at"] = now
    record = next(
        (row for row in runs if str(row.get("discovery_run_id")) == run_id),
        None,
    )
    if record is None:
        record = {
            "discovery_run_id": run_id,
            "topic": topic,
            "started_at": now,
        }
        runs.append(record)
    record["status"] = status
    if status in {"completed", "interrupted", "failed"}:
        record["finished_at"] = now
    manifest["current_discovery_run_id"] = run_id
    manifest["discovery_runs"] = runs
    manifest["updated_at"] = now
    write_json(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a numbered ChemVellum review project or experiment."
    )
    parser.add_argument(
        "--review-root", default=str(Path(__file__).resolve().parents[3])
    )
    subparsers = parser.add_subparsers(dest="kind", required=True)
    review = subparsers.add_parser("review")
    review.add_argument("--topic", required=True)
    review.add_argument("--title", default="")
    review.add_argument("--project-id", default="")
    experiment = subparsers.add_parser("experiment")
    experiment.add_argument("--topic", required=True)
    experiment.add_argument("--experiment-id", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.review_root).resolve()
    if args.kind == "review":
        manifest = ensure_review_project(
            root,
            args.topic,
            title=args.title,
            project_id=args.project_id,
        )
    else:
        manifest = ensure_experiment(
            root,
            args.topic,
            experiment_id=args.experiment_id,
        )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
