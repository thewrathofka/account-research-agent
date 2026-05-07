"""Tool factory — picks the active web-search backend per config.SEARCH_BACKEND.

Exposed for tasks: `from tools import build_search_tool`.

Backends:
- "tavily" (default): WebSearchTool — content-rich snippets in one call
- "brave": BraveSearchTool — cheaper, descriptions only
- "brave_jina": Brave for URL discovery + Jina for full-page Markdown when needed
                (Brave returns the URL list, model decides whether to call read_url)
"""

from __future__ import annotations

import config
from tools.base import Tool


def build_search_tool() -> Tool:
    """Return the configured primary search tool."""
    if config.SEARCH_BACKEND == "tavily":
        from tools.web_search import WebSearchTool
        return WebSearchTool()
    elif config.SEARCH_BACKEND == "brave":
        from tools.brave_search import BraveSearchTool
        return BraveSearchTool()
    elif config.SEARCH_BACKEND == "brave_jina":
        from tools.brave_search import BraveSearchTool
        return BraveSearchTool()
    raise ValueError(
        f"Unknown SEARCH_BACKEND={config.SEARCH_BACKEND!r}. "
        f"Expected one of: tavily | brave | brave_jina."
    )


def build_reader_tool() -> Tool | None:
    """Return a URL-reader tool when the active backend includes one (brave_jina)."""
    if config.SEARCH_BACKEND == "brave_jina":
        from tools.jina_reader import JinaReaderTool
        return JinaReaderTool()
    return None
