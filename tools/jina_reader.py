"""Jina Reader — converts any URL into clean Markdown for LLM consumption.

Pricing (2026): free tier covers 1M tokens/mo + 200 RPM. Effectively free at
our volume. Optional Bearer token via JINA_API_KEY for higher rate limits.

Pattern: GET https://r.jina.ai/<url>  → returns clean Markdown of the page.

Used as a *standalone* tool (separate `read_url` action) and as the content
fetcher for BraveJinaSearchTool (which lists Brave results, then fetches
the most relevant ones via Jina).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

import config
from run_log import ToolCallCache


JINA_READER_SCHEMA: dict[str, Any] = {
    "name": "read_url",
    "description": (
        "Fetch a specific URL and return its content as clean Markdown. Use this "
        "when a search result snippet is too short to answer your question and "
        "you need the full page content."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The full URL to read (https://...)."},
        },
        "required": ["url"],
    },
}


@dataclass
class JinaReaderTool:
    """Wraps Jina Reader (r.jina.ai). 24h cache-backed."""
    api_key: str = field(default_factory=lambda: config.JINA_API_KEY or "")
    cache: ToolCallCache = field(default_factory=ToolCallCache)
    _count: int = 0
    _cache_hits: int = 0
    max_chars: int = 8_000  # truncate very long pages so context stays manageable

    @property
    def name(self) -> str:
        return JINA_READER_SCHEMA["name"]

    @property
    def description(self) -> str:
        return JINA_READER_SCHEMA["description"]

    @property
    def input_schema(self) -> dict[str, Any]:
        return JINA_READER_SCHEMA["input_schema"]

    @property
    def call_count(self) -> int:
        return self._count

    @property
    def cache_hit_count(self) -> int:
        return self._cache_hits

    def __call__(self, url: str) -> str:
        self._count += 1
        args = {"backend": "jina", "url": url}
        cached = self.cache.lookup(self.name, args)
        if cached is not None:
            self._cache_hits += 1
            return cached
        headers: dict[str, str] = {"Accept": "text/plain"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            resp = httpx.get(f"https://r.jina.ai/{url}", headers=headers, timeout=30.0)
            resp.raise_for_status()
            text = resp.text
        except httpx.HTTPError as e:
            return f"ERROR: Jina Reader fetch failed for {url}: {type(e).__name__}: {e}"
        if len(text) > self.max_chars:
            text = text[: self.max_chars] + f"\n\n[truncated at {self.max_chars} chars]"
        self.cache.store(self.name, args, text)
        return text
