"""Brave Search tool — alternative to Tavily.

Brave returns title + URL + description (a short snippet, not full content).
For LLM agent use this is often enough; when full content is needed, pair with
Jina Reader (see tools/jina_reader.py and tools/brave_jina.py).

Pricing (2026): free tier 2K searches/mo; ~$3/1k beyond.
Endpoint: https://api.search.brave.com/res/v1/web/search

Cache-backed via the same ToolCallCache used by WebSearchTool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

import config
from run_log import ToolCallCache


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


@dataclass
class BraveSearchTool:
    """Brave Search wrapper. Drop-in replacement for WebSearchTool (Tavily)."""
    api_key: str = field(default_factory=lambda: config.BRAVE_API_KEY)
    cache: ToolCallCache = field(default_factory=ToolCallCache)
    _count: int = 0
    _cache_hits: int = 0

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

    def __call__(self, query: str) -> str:
        self._count += 1
        args = {"backend": "brave", "query": query}
        cached = self.cache.lookup(self.name, args)
        if cached is not None:
            self._cache_hits += 1
            return cached

        try:
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
        except httpx.HTTPError as e:
            return f"ERROR: Brave search failed: {type(e).__name__}: {e}"

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
        self.cache.store(self.name, args, text)
        return text
