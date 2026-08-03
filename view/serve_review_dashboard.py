#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import posixpath
import re
import shutil
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


SAFE_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


def review_project_path(review_root: Path, project_id: str) -> Path:
    """Return a contained project path for a portable, URL-safe identifier."""
    project_id = str(project_id or "").strip()
    if not SAFE_PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError(f"invalid project id: {project_id!r}")
    base = (review_root / "review-projects").resolve()
    candidate = (base / project_id).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"project path escapes review-projects: {project_id!r}") from exc
    return candidate


class DashboardHandler(BaseHTTPRequestHandler):
    review_root: Path
    library_app_path: Path
    discovery_app_path: Path
    matrix_app_path: Path
    blueprint_app_path: Path
    sections_app_path: Path
    figures_app_path: Path
    draft_app_path: Path
    final_app_path: Path

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    @property
    def metadata_dir(self) -> Path:
        return self.review_root / "review-library" / "metadata" / "papers"

    @property
    def registry_path(self) -> Path:
        return self.review_root / "review-library" / "registry" / "papers.jsonl"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/library")
            self.end_headers()
        elif parsed.path == "/library":
            self.send_file(self.library_app_path, "text/html; charset=utf-8")
        elif parsed.path == "/discovery":
            self.send_file(self.discovery_app_path, "text/html; charset=utf-8")
        elif parsed.path == "/matrix":
            self.send_file(self.matrix_app_path, "text/html; charset=utf-8")
        elif parsed.path == "/blueprint":
            self.send_file(self.blueprint_app_path, "text/html; charset=utf-8")
        elif parsed.path == "/sections":
            self.send_file(self.sections_app_path, "text/html; charset=utf-8")
        elif parsed.path == "/figures":
            self.send_file(self.figures_app_path, "text/html; charset=utf-8")
        elif parsed.path == "/draft":
            self.send_file(self.draft_app_path, "text/html; charset=utf-8")
        elif parsed.path == "/final":
            self.send_file(self.final_app_path, "text/html; charset=utf-8")
        elif parsed.path.startswith("/assets/"):
            self.handle_static_asset(parsed.path)
        elif parsed.path == "/api/projects":
            self.handle_projects()
        elif parsed.path == "/api/papers":
            self.handle_papers()
        elif parsed.path == "/api/discovery-projects":
            self.handle_discovery_projects()
        elif parsed.path.startswith("/api/project/") and parsed.path.endswith("/draft"):
            project_id = unquote(parsed.path.split("/")[3])
            self.handle_project_draft_get(project_id)
        elif parsed.path.startswith("/api/project/"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 4:
                project_id = unquote(parts[2])
                stage = unquote(parts[3])
                self.handle_project_stage_get(project_id, stage)
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "project stage not found")
        elif parsed.path.startswith("/api/discovery/"):
            project_id = unquote(parsed.path.rsplit("/", 1)[-1])
            self.handle_discovery_get(project_id)
        elif parsed.path.startswith("/api/metadata/"):
            paper_id = unquote(parsed.path.rsplit("/", 1)[-1])
            self.handle_metadata_get(paper_id)
        elif parsed.path.startswith("/api/local/metadata/"):
            paper_id = unquote(parsed.path.rsplit("/", 1)[-1])
            self.handle_metadata_get(paper_id)
        elif parsed.path.startswith("/api/markdown/"):
            paper_id = unquote(parsed.path.rsplit("/", 1)[-1])
            self.handle_markdown_get(paper_id)
        elif parsed.path.startswith("/api/local/markdown/"):
            paper_id = unquote(parsed.path.rsplit("/", 1)[-1])
            self.handle_markdown_get(paper_id)
        elif parsed.path == "/file":
            query = parse_qs(parsed.query)
            path = query.get("path", [""])[0]
            paper_id = query.get("paper_id", [""])[0]
            self.handle_file(path, paper_id)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/metadata/"):
            paper_id = unquote(parsed.path.rsplit("/", 1)[-1])
            self.handle_metadata_put(paper_id)
            return
        if parsed.path.startswith("/api/project/") and parsed.path.endswith("/draft"):
            project_id = unquote(parsed.path.split("/")[3])
            self.handle_project_draft_put(project_id)
            return
        if parsed.path.startswith("/api/discovery/"):
            project_id = unquote(parsed.path.rsplit("/", 1)[-1])
            query = parse_qs(parsed.query)
            self.handle_discovery_put(project_id, confirm=bool(query.get("confirm")))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/project/") and parsed.path.endswith("/export-docx"):
            project_id = unquote(parsed.path.split("/")[3])
            self.handle_project_export_docx(project_id)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def handle_project_export_docx(self, project_id: str) -> None:
        try:
            project = review_project_path(self.review_root, project_id)
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        deliverables = project / "deliverables"
        candidates = [
            deliverables / "review.md",
            project / "manuscript.with-assets.md",
            project / "manuscript.md",
            project / "05_final_audit" / "final_draft.md",
        ]
        md_path = next((path for path in candidates if path.exists()), None)
        if md_path is None:
            self.send_error(HTTPStatus.BAD_REQUEST, "no manuscript markdown found")
            return
        deliverables.mkdir(parents=True, exist_ok=True)
        docx_path = deliverables / "review.docx"
        script = self.review_root / "skills" / "review-export-docx" / "scripts" / "md2docx.py"
        if not script.exists():
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "md2docx.py not found")
            return
        try:
            result = subprocess.run(
                [sys.executable, str(script), "--input", str(md_path), "--output", str(docx_path)],
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            self.send_error(HTTPStatus.GATEWAY_TIMEOUT, "docx export timeout")
            return
        if result.returncode != 0 or not docx_path.exists():
            tail = (result.stderr or result.stdout or "").strip().splitlines()[-20:]
            self.send_json({
                "ok": False,
                "returncode": result.returncode,
                "error": "\n".join(tail) or "md2docx.py failed",
            })
            return
        self.send_json({
            "ok": True,
            "path": str(docx_path),
            "size": docx_path.stat().st_size,
        })

    def handle_projects(self) -> None:
        self.send_json(list_review_projects(self.review_root))

    def handle_papers(self) -> None:
        papers = []
        for path in sorted(self.metadata_dir.glob("*.metadata.json")):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            structured_tags = value_of(meta.get("structured_tags")) or {}
            structured_values = list(structured_tags.values()) if isinstance(structured_tags, dict) else []
            papers.append(
                {
                    "paper_id": meta.get("paper_id"),
                    "title": value_of(meta.get("title")),
                    "authors": value_of(meta.get("authors")) or [],
                    "year": value_of(meta.get("year")),
                    "journal": value_of(meta.get("journal")),
                    "doi": value_of(meta.get("doi")),
                    "structured_tags": structured_tags,
                    "tags": structured_values,
                    "human_review_status": (meta.get("human_review") or {}).get("status"),
                    "needs_human_check": (meta.get("quality") or {}).get("needs_human_check"),
                }
            )
        self.send_json(papers)

    def handle_discovery_projects(self) -> None:
        self.send_json([p for p in list_review_projects(self.review_root) if p.get("has_discovery")])

    def handle_discovery_get(self, project_id: str) -> None:
        try:
            path = self.discovery_path(project_id)
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "discovery data not found")
            return
        self.send_file(path, "application/json; charset=utf-8")

    def handle_discovery_put(self, project_id: str, confirm: bool = False) -> None:
        try:
            path = self.discovery_path(project_id)
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, f"invalid discovery json: {exc}")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        selected = selected_from_combined(data.get("results", []), project_id)
        selected["human_confirmed"] = bool(confirm)
        (path.parent / "selected_discovery_results.json").write_text(
            json.dumps(selected, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (path.parent / "human_check_state.json").write_text(
            json.dumps(
                {
                    "project_id": project_id,
                    "status": "confirmed" if confirm else "pending",
                    "confirmed_at": now_utc() if confirm else None,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.send_json({"ok": True, "confirmed": confirm})

    def handle_project_draft_get(self, project_id: str) -> None:
        try:
            project = review_project_path(self.review_root, project_id)
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if not project.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "project not found")
            return
        self.send_json(project_draft_payload(self.review_root, project_id))

    def handle_project_stage_get(self, project_id: str, stage: str) -> None:
        try:
            project = review_project_path(self.review_root, project_id)
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if not project.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "project not found")
            return
        payloads = {
            "matrix": project_matrix_payload,
            "blueprint": project_blueprint_payload,
            "sections": project_sections_payload,
            "figures": project_figures_payload,
            "final": project_final_payload,
        }
        builder = payloads.get(stage)
        if not builder:
            self.send_error(HTTPStatus.NOT_FOUND, "unknown stage")
            return
        self.send_json(builder(self.review_root, project_id))

    def handle_static_asset(self, path: str) -> None:
        assets_root = Path(__file__).resolve().parent / "assets"
        rel = posixpath.normpath(unquote(path.removeprefix("/assets/"))).lstrip("/")
        candidate = (assets_root / rel).resolve()
        try:
            candidate.relative_to(assets_root.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN, "asset path outside assets root")
            return
        if not candidate.exists() or not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "asset not found")
            return
        mime = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        self.send_file(candidate, mime)

    def handle_project_draft_put(self, project_id: str) -> None:
        try:
            project = review_project_path(self.review_root, project_id)
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if not project.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "project not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, f"invalid draft payload: {exc}")
            return
        notes_dir = project / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        if "first_draft_md" in data:
            (project / "manuscript.md").write_text(str(data.get("first_draft_md") or ""), encoding="utf-8")
        if "merge_report_md" in data:
            (notes_dir / "merge_report.md").write_text(str(data.get("merge_report_md") or ""), encoding="utf-8")
        if "remaining_issues_md" in data:
            (notes_dir / "remaining_issues.md").write_text(str(data.get("remaining_issues_md") or ""), encoding="utf-8")
        if "draft_bundle" in data and isinstance(data.get("draft_bundle"), dict):
            write_json(notes_dir / "draft_bundle.json", data["draft_bundle"])
        self.send_json({"ok": True, "project_id": project_id})

    def discovery_path(self, project_id: str) -> Path:
        return review_project_path(self.review_root, project_id) / "00_discovery" / "combined_results_by_keyword.json"

    def handle_metadata_get(self, paper_id: str) -> None:
        path = self.metadata_dir / f"{paper_id}.metadata.json"
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "metadata not found")
            return
        self.send_file(path, "application/json; charset=utf-8")

    def handle_metadata_put(self, paper_id: str) -> None:
        path = self.metadata_dir / f"{paper_id}.metadata.json"
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, f"invalid json: {exc}")
            return
        if data.get("paper_id") != paper_id:
            self.send_error(HTTPStatus.BAD_REQUEST, "paper_id mismatch")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        rebuild_registry(self.review_root)
        self.send_json({"ok": True})

    def handle_markdown_get(self, paper_id: str) -> None:
        meta = self.load_meta(paper_id)
        if not meta:
            self.send_error(HTTPStatus.NOT_FOUND, "metadata not found")
            return
        path_value = (meta.get("source_paths") or {}).get("markdown")
        if not path_value:
            self.send_error(HTTPStatus.NOT_FOUND, "markdown path missing")
            return
        path = resolve_review_path(self.review_root, path_value)
        if not path or not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "markdown not found")
            return
        self.send_file(path, "text/markdown; charset=utf-8")

    def handle_file(self, raw_path: str, paper_id: str = "") -> None:
        if not raw_path:
            self.send_error(HTTPStatus.BAD_REQUEST, "missing path")
            return
        path = safe_abs_path(raw_path)
        if not path:
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid path")
            return
        if not path.is_absolute():
            resolved = None
            if paper_id:
                meta = self.load_meta(paper_id)
                md_value = ((meta or {}).get("source_paths") or {}).get("markdown")
                if md_value:
                    markdown_path = resolve_review_path(self.review_root, md_value)
                    if markdown_path is not None:
                        candidate = (markdown_path.parent / path).resolve()
                        if candidate.exists():
                            resolved = candidate
            path = resolved or (self.review_root / path).resolve()
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "file not found")
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_file(path, ctype)

    def load_meta(self, paper_id: str) -> dict | None:
        path = self.metadata_dir / f"{paper_id}.metadata.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def send_json(self, data: object) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_file(self, path: Path, content_type: str) -> None:
        try:
            size = path.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with path.open("rb") as f:
                shutil.copyfileobj(f, self.wfile)
        except BrokenPipeError:
            pass


def value_of(field):
    if isinstance(field, dict) and "value" in field:
        return field.get("value")
    return field


def safe_abs_path(raw: str) -> Path | None:
    raw = unquote(raw)
    if "\x00" in raw:
        return None
    # Keep spaces and unicode; only normalize separators.
    raw = posixpath.normpath(raw)
    return Path(raw).expanduser().resolve() if raw.startswith("/") else Path(raw)


def resolve_review_path(review_root: Path, raw: str) -> Path | None:
    path = safe_abs_path(raw)
    if path is None:
        return None
    return path if path.is_absolute() else (review_root / path).resolve()


def rebuild_registry(review_root: Path) -> None:
    meta_dir = review_root / "review-library" / "metadata" / "papers"
    registry = review_root / "review-library" / "registry" / "papers.jsonl"
    rows = []
    for path in sorted(meta_dir.glob("*.metadata.json")):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        source_paths = meta.get("source_paths") or {}
        rows.append(
            {
                "paper_id": meta.get("paper_id"),
                "slug": meta.get("slug"),
                "title": value_of(meta.get("title")),
                "authors": value_of(meta.get("authors")),
                "year": value_of(meta.get("year")),
                "journal": value_of(meta.get("journal")),
                "doi": value_of(meta.get("doi")),
                "source_pdf": source_paths.get("pdf"),
                "markdown_path": source_paths.get("markdown"),
                "content_list_path": source_paths.get("content_list"),
                "metadata_path": path.relative_to(review_root).as_posix(),
                "parse_status": "done",
                "human_review_status": (meta.get("human_review") or {}).get("status"),
                "needs_human_check": (meta.get("quality") or {}).get("needs_human_check"),
            }
        )
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def now_utc() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def selected_from_combined(groups: list[dict], project_id: str) -> dict:
    selected = {"project_id": project_id, "keywords": [], "local_papers": {}, "web_papers": []}
    for group in groups:
        if group.get("keep") is False:
            continue
        selected["keywords"].append({"keyword": group.get("keyword"), "category": group.get("category")})
        for row in group.get("local_results", []):
            if row.get("keep") is False:
                continue
            pid = row.get("paper_id")
            if not pid:
                continue
            item = selected["local_papers"].setdefault(
                pid,
                {
                    "paper_id": pid,
                    "title": row.get("title"),
                    "year": row.get("year"),
                    "journal": row.get("journal"),
                    "role": row.get("role", "uncertain"),
                    "matched_keywords": [],
                    "best_score": 0,
                    "keep": True,
                },
            )
            item["matched_keywords"].append(group.get("keyword"))
            item["best_score"] = max(item.get("best_score", 0), row.get("score", 0))
        for row in group.get("web_results", []):
            if row.get("keep") is not False:
                selected["web_papers"].append({**row, "matched_keyword": group.get("keyword")})
    selected["local_papers"] = sorted(
        selected["local_papers"].values(), key=lambda x: x.get("best_score", 0), reverse=True
    )[:30]
    selected["web_papers"] = selected["web_papers"][:30]
    return selected


def read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def read_json_if_exists(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def infer_project_topic(project: Path) -> str:
    manifest = read_json_if_exists(project / "project.json")
    if isinstance(manifest, dict):
        if manifest.get("title"):
            return str(manifest.get("title"))
        if manifest.get("topic"):
            return str(manifest.get("topic"))
    discovery = read_json_if_exists(project / "00_discovery" / "combined_results_by_keyword.json")
    if isinstance(discovery, dict) and discovery.get("topic"):
        return str(discovery.get("topic"))
    topic_input = project / "00_discovery" / "topic_input.md"
    if topic_input.exists():
        for line in topic_input.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
    manuscript = project / "manuscript.md"
    if manuscript.exists():
        for line in manuscript.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
    bundle = read_json_if_exists(project / "notes" / "draft_bundle.json")
    if not isinstance(bundle, dict):
        bundle = read_json_if_exists(project / "04_first_draft" / "draft_bundle.json")
    if isinstance(bundle, dict) and bundle.get("topic"):
        return str(bundle.get("topic"))
    return ""


def list_review_projects(review_root: Path) -> list[dict[str, Any]]:
    base = review_root / "review-projects"
    projects: list[dict[str, Any]] = []
    if not base.exists():
        return projects
    for project in sorted(p for p in base.iterdir() if p.is_dir()):
        discovery_state = read_json_if_exists(project / "00_discovery" / "human_check_state.json") or {}
        manifest = read_json_if_exists(project / "project.json") or {}
        assets_dir = project / "assets"
        deliverables_dir = project / "deliverables"
        has_assets = assets_dir.exists() and any(path.is_file() for path in assets_dir.rglob("*"))
        has_deliverables = deliverables_dir.exists() and any(path.is_file() for path in deliverables_dir.rglob("*"))
        has_manuscript = (project / "manuscript.md").exists()
        run_count = len([path for path in (project / "runs").iterdir() if path.is_dir()]) if (project / "runs").exists() else 0
        projects.append(
            {
                "project_id": project.name,
                "topic": infer_project_topic(project),
                "has_discovery": (project / "00_discovery" / "combined_results_by_keyword.json").exists(),
                "discovery_status": discovery_state.get("status") or "pending",
                "has_matrix_outline": (project / "01_matrix_outline" / "literature_matrix.json").exists(),
                "has_blueprint": (project / "01_matrix_outline" / "section_blueprint.json").exists(),
                "has_section_drafting": (project / "02_section_drafting" / "section_drafts.md").exists(),
                "has_assets": has_assets,
                "has_manuscript": has_manuscript,
                "has_deliverables": has_deliverables,
                "current_discovery_run_id": manifest.get("current_discovery_run_id"),
                "archived_run_count": run_count,
                "has_figure_redraw": has_assets or (project / "03_figure_redraw" / "redrawn_figure_manifest.json").exists(),
                "has_first_draft": has_manuscript or (project / "04_first_draft" / "first_draft.md").exists(),
                "has_final_audit": has_deliverables or (project / "05_final_audit" / "final_draft.md").exists(),
            }
        )
    return projects


def project_summary(review_root: Path, project_id: str) -> dict[str, Any] | None:
    return next((p for p in list_review_projects(review_root) if p["project_id"] == project_id), None)


def project_matrix_payload(review_root: Path, project_id: str) -> dict[str, Any]:
    project = review_project_path(review_root, project_id)
    stage = project / "01_matrix_outline"
    return {
        "project_id": project_id,
        "topic": infer_project_topic(project),
        "summary": project_summary(review_root, project_id),
        "paper_reading_notes": read_json_if_exists(stage / "paper_reading_notes.json"),
        "literature_matrix": read_json_if_exists(stage / "literature_matrix.json"),
        "literature_matrix_csv": read_text_if_exists(stage / "literature_matrix.csv"),
        "outline_options_md": read_text_if_exists(stage / "outline_options.md"),
        "selected_outline_md": read_text_if_exists(stage / "selected_outline.md"),
        "matrix_outline_report_md": read_text_if_exists(stage / "matrix_outline_report.md"),
        "paths": {"stage_dir": str(stage)},
    }


def project_blueprint_payload(review_root: Path, project_id: str) -> dict[str, Any]:
    project = review_project_path(review_root, project_id)
    stage = project / "01_matrix_outline"
    return {
        "project_id": project_id,
        "topic": infer_project_topic(project),
        "summary": project_summary(review_root, project_id),
        "section_blueprint": read_json_if_exists(stage / "section_blueprint.json"),
        "section_writing_plan_md": read_text_if_exists(stage / "section_writing_plan.md"),
        "selected_outline_md": read_text_if_exists(stage / "selected_outline.md"),
        "paths": {"stage_dir": str(stage)},
    }


def project_sections_payload(review_root: Path, project_id: str) -> dict[str, Any]:
    project = review_project_path(review_root, project_id)
    stage = project / "02_section_drafting"
    section_files = []
    sections_dir = stage / "sections"
    if sections_dir.exists():
        for path in sorted(sections_dir.glob("*.md")):
            section_files.append({"name": path.name, "path": str(path), "content": read_text_if_exists(path)})
    return {
        "project_id": project_id,
        "topic": infer_project_topic(project),
        "summary": project_summary(review_root, project_id),
        "section_tasks": read_json_if_exists(stage / "section_tasks.json"),
        "section_drafts": read_json_if_exists(stage / "section_drafts.json"),
        "section_drafts_md": read_text_if_exists(stage / "section_drafts.md"),
        "section_files": section_files,
        "paper_figure_candidates": read_json_if_exists(stage / "paper_figure_candidates.json"),
        "figure_candidates": read_json_if_exists(stage / "figure_candidates.json"),
        "section_drafting_report_md": read_text_if_exists(stage / "section_drafting_report.md"),
        "paths": {"stage_dir": str(stage), "sections_dir": str(sections_dir)},
    }


def project_figures_payload(review_root: Path, project_id: str) -> dict[str, Any]:
    project = review_project_path(review_root, project_id)
    draft_stage = project / "02_section_drafting"
    stage = project / "03_figure_redraw"
    assets = project / "assets"
    return {
        "project_id": project_id,
        "topic": infer_project_topic(project),
        "summary": project_summary(review_root, project_id),
        "figure_candidates": read_json_if_exists(draft_stage / "figure_candidates.json"),
        "redrawn_manifest": (
            read_json_if_exists(assets / "asset_manifest.json")
            or read_json_if_exists(stage / "redrawn_figure_manifest.json")
        ),
        "paper_figure_inventory": read_json_if_exists(assets / "paper_figure_inventory.json"),
        "insertion_report": read_json_if_exists(assets / "insertion_report.json"),
        "figure_redraw_report_md": (
            read_text_if_exists(assets / "figure_notes.md")
            or read_text_if_exists(stage / "figure_redraw_report.md")
        ),
        "paths": {"stage_dir": str(assets), "draft_stage_dir": str(draft_stage)},
    }


def project_final_payload(review_root: Path, project_id: str) -> dict[str, Any]:
    project = review_project_path(review_root, project_id)
    deliverables = project / "deliverables"
    legacy_stage = project / "05_final_audit"
    docx_candidates = sorted(deliverables.glob("*.docx")) if deliverables.exists() else []
    docx_path = docx_candidates[0] if docx_candidates else legacy_stage / "final_draft.docx"
    final_md = deliverables / "review.md"
    if not final_md.exists():
        final_md = legacy_stage / "final_draft.md"
    return {
        "project_id": project_id,
        "topic": infer_project_topic(project),
        "summary": project_summary(review_root, project_id),
        "final_draft_md": read_text_if_exists(final_md),
        "final_audit_report_md": (
            read_text_if_exists(deliverables / "final_audit_report.md")
            or read_text_if_exists(legacy_stage / "final_audit_report.md")
        ),
        "release_report_md": (
            read_text_if_exists(deliverables / "release_report.md")
            or read_text_if_exists(legacy_stage / "release_report.md")
        ),
        "final_draft_docx_path": str(docx_path),
        "final_draft_docx_exists": docx_path.exists(),
        "paths": {"stage_dir": str(deliverables)},
    }


def project_draft_payload(review_root: Path, project_id: str) -> dict[str, Any]:
    project = review_project_path(review_root, project_id)
    notes_dir = project / "notes"
    legacy_stage = project / "04_first_draft"
    figures_manifest = (
        read_json_if_exists(project / "assets" / "asset_manifest.json")
        or read_json_if_exists(project / "03_figure_redraw" / "redrawn_figure_manifest.json")
        or {}
    )
    draft_bundle = read_json_if_exists(notes_dir / "draft_bundle.json") or read_json_if_exists(legacy_stage / "draft_bundle.json")
    section_drafts = read_json_if_exists(project / "02_section_drafting" / "section_drafts.json")
    redrawn = []
    for row in (figures_manifest.get("figures") or []):
        if isinstance(row, dict):
            redrawn.append(row)
    return {
        "project_id": project_id,
        "topic": infer_project_topic(project),
        "summary": next((p for p in list_review_projects(review_root) if p["project_id"] == project_id), None),
        "draft_bundle": draft_bundle,
        "first_draft_md": read_text_if_exists(project / "manuscript.md") or read_text_if_exists(legacy_stage / "first_draft.md"),
        "merge_report_md": read_text_if_exists(notes_dir / "merge_report.md") or read_text_if_exists(legacy_stage / "merge_report.md"),
        "remaining_issues_md": read_text_if_exists(notes_dir / "remaining_issues.md") or read_text_if_exists(legacy_stage / "remaining_issues.md"),
        "section_drafts": section_drafts,
        "redrawn_figures": redrawn,
        "paths": {
            "stage_dir": str(project),
            "first_draft_base_dir": str(project),
            "first_draft": str(project / "manuscript.md"),
            "merge_report": str(notes_dir / "merge_report.md"),
            "remaining_issues": str(notes_dir / "remaining_issues.md"),
        },
    }


def dashboard_assets(view_root: Path) -> tuple[Path, ...]:
    dashboard = view_root / "assets" / "dashboard"
    library_path = dashboard / "library.html"
    discovery_path = dashboard / "discovery.html"
    matrix_path = dashboard / "matrix.html"
    blueprint_path = dashboard / "blueprint.html"
    sections_path = dashboard / "sections.html"
    figures_path = dashboard / "figures.html"
    draft_path = dashboard / "draft.html"
    final_path = dashboard / "final.html"
    paths = [library_path, discovery_path, matrix_path, blueprint_path, sections_path, figures_path, draft_path, final_path]
    if any(not path.exists() for path in paths):
        raise FileNotFoundError(f"dashboard assets not found under {view_root / 'assets' / 'dashboard'}")
    return tuple(paths)


def run(args: argparse.Namespace) -> int:
    review_root = Path(args.review_root).resolve()
    view_root = Path(__file__).resolve().parent
    (
        library_app_path,
        discovery_app_path,
        matrix_app_path,
        blueprint_app_path,
        sections_app_path,
        figures_app_path,
        draft_app_path,
        final_app_path,
    ) = dashboard_assets(view_root)
    if not (review_root / "review-library" / "metadata" / "papers").exists():
        print("ERROR: metadata files not found. Run prepare_metadata.py first.", file=sys.stderr)
        return 2
    DashboardHandler.review_root = review_root
    DashboardHandler.library_app_path = library_app_path
    DashboardHandler.discovery_app_path = discovery_app_path
    DashboardHandler.matrix_app_path = matrix_app_path
    DashboardHandler.blueprint_app_path = blueprint_app_path
    DashboardHandler.sections_app_path = sections_app_path
    DashboardHandler.figures_app_path = figures_app_path
    DashboardHandler.draft_app_path = draft_app_path
    DashboardHandler.final_app_path = final_app_path
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Serving dashboard at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve local review metadata dashboard.")
    parser.add_argument("--review-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
