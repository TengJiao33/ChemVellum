"""Minimal hosted SciAtlas client used by discover.py.

Wraps `/v1/search` and `/healthz`. Auth via `Authorization: Bearer` +
`X-API-Key`. No dependency on the upstream `sciatlas` pip package.

Env vars:
  SCIATLAS_API_BASE_URL   (default http://sciatlas.openkg.cn)
  SCIATLAS_API_KEY        (required for /v1/search)
  SCIATLAS_TIMEOUT        (seconds, default 60)
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_BASE_URL = "http://sciatlas.openkg.cn"
DEFAULT_TIMEOUT = 240


@dataclass(frozen=True)
class SciAtlasConfig:
    base_url: str
    api_key: str
    timeout: int

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url)


def load_config(
    *, base_url: str | None = None, api_key: str | None = None, timeout: int | None = None
) -> SciAtlasConfig:
    return SciAtlasConfig(
        base_url=(base_url or os.environ.get("SCIATLAS_API_BASE_URL") or DEFAULT_BASE_URL).strip(),
        api_key=(api_key or os.environ.get("SCIATLAS_API_KEY") or "").strip(),
        timeout=int(timeout or os.environ.get("SCIATLAS_TIMEOUT") or DEFAULT_TIMEOUT),
    )


class SciAtlasClient:
    def __init__(self, config: SciAtlasConfig | None = None) -> None:
        self.config = config or load_config()

    def _request(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        url = self.config.base_url.rstrip("/") + "/" + endpoint.lstrip("/")
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if endpoint.rstrip("/") != "/healthz":
            if not self.config.api_key:
                raise RuntimeError("SCIATLAS_API_KEY is required for this endpoint.")
            headers["Authorization"] = f"Bearer {self.config.api_key}"
            headers["X-API-Key"] = self.config.api_key
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw)
            except json.JSONDecodeError:
                detail = raw
            raise RuntimeError(f"SciAtlas API error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"SciAtlas connection error: {exc.reason}") from exc

    def health(self) -> Any:
        return self._request("GET", "/healthz")

    def search_papers(
        self,
        *,
        query: str,
        keyword: str,
        keyword_score: int = 10,
        top_k: int = 8,
        retrieval_mode: str = "hybrid",
        time_range: str | None = None,
        domain: str | None = None,
        extra_options: dict[str, Any] | None = None,
    ) -> Any:
        """Call `/v1/search` for one keyword anchor and return the raw response."""
        keywords = [{"text": keyword, "score": keyword_score}]
        # Plan follows the structure produced by `sciatlas search-papers`.
        plan: dict[str, Any] = {
            "query_text": query,
            "source_type": "idea_text",
            "source_title": None,
            "keywords": keywords,
            "titles": [],
            "reference_titles": [],
        }
        if domain:
            plan["domain"] = domain
        if time_range:
            plan["time_range"] = time_range
        options: dict[str, Any] = {
            "top_k": top_k,
            "retrieval_mode": retrieval_mode,
        }
        if extra_options:
            options.update(extra_options)
        return self._request("POST", "/v1/search", {"plan": plan, "options": options})


def _walk_paper_lists(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, list):
        return [p for p in node if isinstance(p, dict)]
    if not isinstance(node, dict):
        return []
    for key in ("papers", "results", "items"):
        out = _walk_paper_lists(node.get(key))
        if out:
            return out
    for nested_key in ("result", "data", "ranking", "sources", "kg", "vector", "web"):
        out = _walk_paper_lists(node.get(nested_key))
        if out:
            return out
    return []

def papers_from_response(response: Any) -> list[dict[str, Any]]:
    """Extract paper records from a /v1/search response.

    Real shape: response.result.ranking.papers (preferred) or
    response.result.sources.kg.papers. Older versions wrap directly under
    response.papers. We walk known containers and return the first non-empty list.
    """
    return _walk_paper_lists(response)
