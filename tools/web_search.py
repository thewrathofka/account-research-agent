"""Tavily web-search tool. One instance per task → per-task call count for cost tracking.

Now backed by a 24h SQLite cache (ToolCallCache) so a query repeated within
24h returns the cached response rather than hitting the Tavily API again. This
matters when modules ask overlapping questions about the same company in the
same monthly batch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tavily import TavilyClient

import config
from run_log import ToolCallCache


WEB_SEARCH_SCHEMA: dict[str, Any] = {
    "name": "web_search",
    "description": (
        "Search the web for information about a company. "
        "Use this to find facts about what the company does, its industry, size, "
        "headquarters, recent news, hiring activity, and competitors. "
        "Make multiple searches with different queries to gather comprehensive info."
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
class WebSearchTool:
    """Tavily search wrapper with 24h SQLite cache. Tracks call count for cost gating."""
    client: TavilyClient = field(
        default_factory=lambda: TavilyClient(api_key=config.TAVILY_API_KEY)
    )
    cache: ToolCallCache = field(default_factory=ToolCallCache)
    _count: int = 0
    _cache_hits: int = 0

    @property
    def name(self) -> str:
        return WEB_SEARCH_SCHEMA["name"]

    @property
    def description(self) -> str:
        return WEB_SEARCH_SCHEMA["description"]

    @property
    def input_schema(self) -> dict[str, Any]:
        return WEB_SEARCH_SCHEMA["input_schema"]

    @property
    def call_count(self) -> int:
        return self._count

    @property
    def cache_hit_count(self) -> int:
        return self._cache_hits

    def __call__(self, query: str) -> str:
        self._count += 1
        # Tavily rejects queries over ~400 chars with HTTP 400. The model occasionally
        # constructs an over-long query (especially in module_09 where it stitches
        # together job titles). Truncate at 350 to leave headroom.
        if len(query) > 350:
            query = query[:347] + "..."
        args = {"query": query}
        # Cache lookup first — same query within 24h returns instantly, no API call.
        cached = self.cache.lookup(self.name, args)
        if cached is not None:
            self._cache_hits += 1
            return cached
        # Cache miss → real Tavily call.
        response = self.client.search(query=query, search_depth="basic", max_results=5)
        results = response.get("results", [])
        if not results:
            text = f"No results for: {query}"
        else:
            lines = []
            for i, r in enumerate(results, start=1):
                lines.append(
                    f"Result {i}:\n"
                    f"Title: {r.get('title', 'No title')}\n"
                    f"URL: {r.get('url', 'No url')}\n"
                    f"Content: {r.get('content', 'No content')}"
                )
            text = "\n\n".join(lines)
        # Write through to cache for future calls.
        self.cache.store(self.name, args, text)
        return text
