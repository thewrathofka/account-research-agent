"""Brave Search tool — alternative to Tavily. Drop-in replacement for WebSearchTool.

Same hard-cap, query-length-reject, transient-error-TTL contract as web_search.py
(Fix Appendix #8/#9/#10/#11). Records observed URLs for source verification (#6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

import config
from rate_limit import BRAVE_LIMITER
from run_log import ToolCallCache
from tools.web_search import QUERY_MAX_CHARS, _extract_urls


BRAVE_SEARCH_SCHEMA: dict[str, Any] = {
    "name": "web_search",  # same name as the Tavily tool — drop-in replacement
    "description": (
        "Search the web for information about a company. Returns title + URL + "
        "short description per result. Make multiple targeted queries to gather "
        "comprehensive info."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A specific, targeted search query."},
        },
        "required": ["query"],
    },
}

BRAVE_SEARCH_TOOL_VERSION = "brave_search_v2"


@dataclass
class BraveSearchTool:
    """Brave Search wrapper. Drop-in replacement for WebSearchTool (Tavily)."""
    api_key: str = field(default_factory=lambda: config.BRAVE_API_KEY)
    cache: ToolCallCache = field(default_factory=ToolCallCache)
    _count: int = 0
    _cache_hits: int = 0
    _observed_urls: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return BRAVE_SEARCH_SCHEMA["name"]

    @property
    def description(self) -> str:
        return BRAVE_SEARCH_SCHEMA["description"]

    @property
    def input_schema(self) -> dict[str, Any]:
        return BRAVE_SEARCH_SCHEMA["input_schema"]

    @property
    def call_count(self) -> int:
        return self._count

    @property
    def cache_hit_count(self) -> int:
        return self._cache_hits

    @property
    def observed_urls(self) -> list[str]:
        return list(self._observed_urls)

    def _record_urls(self, urls: list[str]) -> None:
        for u in urls:
            if u and u not in self._observed_urls:
                self._observed_urls.append(u)

    def __call__(self, query: str) -> str:
        if self._count >= config.TAVILY_SEARCHES_PER_TASK_CAP:
            return (
                f"ERROR: search cap reached for this task "
                f"({config.TAVILY_SEARCHES_PER_TASK_CAP}). "
                "Produce best-effort output with low confidence."
            )
        if len(query) > QUERY_MAX_CHARS:
            return (
                f"ERROR: query too long ({len(query)} chars). "
                f"Rewrite as a concise web search query under {QUERY_MAX_CHARS} "
                "characters and call web_search again."
            )
        self._count += 1
        args = {
            "backend": "brave",
            "query": query,
            "max_results": 5,
            "tool_version": BRAVE_SEARCH_TOOL_VERSION,
        }
        cached = self.cache.lookup(self.name, args)
        if cached is not None:
            self._cache_hits += 1
            self._record_urls(_extract_urls(cached))
            return cached

        try:
            with BRAVE_LIMITER:
                resp = httpx.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": 5},
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": self.api_key,
                    },
                    timeout=15.0,
                )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else None
            if status in (408, 425, 429, 500, 502, 503, 504):
                text = f"ERROR: retryable search failure: {status}"
                self.cache.store(self.name, args, text, ttl_minutes=30)
                return text
            return f"ERROR: Brave search failed: {type(e).__name__}: {e}"
        except (httpx.HTTPError, TimeoutError) as e:
            text = f"ERROR: retryable search failure: {type(e).__name__}"
            self.cache.store(self.name, args, text, ttl_minutes=30)
            return text

        items = (data.get("web") or {}).get("results", []) or []
        if not items:
            text = f"No results for: {query}"
        else:
            lines = []
            for i, r in enumerate(items[:5], start=1):
                lines.append(
                    f"Result {i}:\n"
                    f"Title: {r.get('title', 'No title')}\n"
                    f"URL: {r.get('url', 'No url')}\n"
                    f"Content: {r.get('description', 'No description')}"
                )
            text = "\n\n".join(lines)
        self.cache.store(self.name, args, text, ttl_hours=24)
        self._record_urls([r.get("url", "") for r in items[:5]])
        return text
