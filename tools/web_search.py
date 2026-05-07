"""Tavily web-search tool. One instance per task → per-task call count for cost tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tavily import TavilyClient

import config


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
    """Tavily search wrapper. Tracks call count for cost gating."""
    client: TavilyClient = field(
        default_factory=lambda: TavilyClient(api_key=config.TAVILY_API_KEY)
    )
    _count: int = 0

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

    def __call__(self, query: str) -> str:
        self._count += 1
        response = self.client.search(query=query, search_depth="basic", max_results=5)
        results = response.get("results", [])
        if not results:
            return f"No results for: {query}"
        lines = []
        for i, r in enumerate(results, start=1):
            lines.append(
                f"Result {i}:\n"
                f"Title: {r.get('title', 'No title')}\n"
                f"URL: {r.get('url', 'No url')}\n"
                f"Content: {r.get('content', 'No content')}"
            )
        return "\n\n".join(lines)
