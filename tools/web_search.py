"""Tavily web-search tool. One instance per task → per-task call count for cost tracking.

Backed by a SQLite cache (ToolCallCache). Successful searches stay 24h; transient
errors (429/5xx/network) cache for 30 minutes only so a brief upstream blip cannot
poison every subsequent call (Fix Appendix #9). Cache key includes backend +
tool_version so backend or schema changes invalidate stale entries (Fix #8).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

import httpx
from tavily import TavilyClient

import config
from rate_limit import TAVILY_LIMITER
from run_log import ToolCallCache


# ---- Quota-exhaustion circuit breaker (2026-05-12) ----
#
# Tavily returns HTTP 432 ("Plan limit exceeded") once the monthly search quota
# is gone. Without a circuit breaker, every remaining task in the batch makes
# its own Tavily call, gets the same 432, and ALSO burns Anthropic tokens
# running its agent loop on what becomes empty/error context. On a 213-account
# run, that's potentially $100+ of wasted Anthropic spend after the quota dies.
#
# The breaker is a process-wide threading.Event set the first time any
# WebSearchTool instance sees a 432. Subsequent calls (in the same Python
# process, across all threads) short-circuit to an ERROR string without
# hitting the API. The error string mirrors the cap-reached one so prompts
# already understand "produce best-effort output with low confidence."
#
# Reset: process exit. There is no auto-reset on a successful call because
# Tavily's quota is a monthly window — once tripped, you need to top up the
# plan, which involves a key/plan refresh outside this process anyway.
_TAVILY_QUOTA_EXHAUSTED = threading.Event()


def tavily_quota_exhausted() -> bool:
    """True iff a Tavily 432 has been observed in this Python process."""
    return _TAVILY_QUOTA_EXHAUSTED.is_set()


def _signal_tavily_quota_exhausted() -> None:
    _TAVILY_QUOTA_EXHAUSTED.set()


def _reset_tavily_quota_flag_for_tests() -> None:
    """Test-only: clear the breaker so independent test cases don't bleed state."""
    _TAVILY_QUOTA_EXHAUSTED.clear()


WEB_SEARCH_SCHEMA: dict[str, Any] = {
    "name": "web_search",
    "description": (
        "Search the web for information about a company. "
        "Use this to find facts about what the company does, its industry, size, "
        "headquarters, recent news, hiring activity, and competitors. "
        "Make multiple searches with different queries to gather comprehensive info. "
        "For news/events queries, ALWAYS pass `days` to bound recency at the API "
        "level (e.g. days=90 for last 3 months, days=180 for last 6 months). "
        "Without `days` Tavily can return year-old results that look fresh."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A specific, targeted search query."},
            "days": {
                "type": "integer",
                "description": (
                    "Recency cutoff in days for news/event queries. Use 90 for "
                    "the preferred 3-month window, 180 for the maximum 6-month "
                    "fallback. Omit for evergreen queries (HQ, employee count, "
                    "competitor list)."
                ),
                "minimum": 1,
                "maximum": 365,
            },
        },
        "required": ["query"],
    },
}

# Bumped when the cache-payload shape changes (e.g. result formatting). Old
# cache entries with a different version are treated as misses (Fix Appendix #8).
# v3 adds the `days` recency parameter to the cache key.
WEB_SEARCH_TOOL_VERSION = "web_search_v3"

# Tavily rejects queries over ~400 chars with HTTP 400. We cut off below that
# and surface a structured error to the model so it issues a tighter query
# (Fix Appendix #11) — silent truncation changes semantics and breaks E4.
QUERY_MAX_CHARS = 350


@dataclass
class WebSearchTool:
    """Tavily search wrapper with cache + per-task hard cap + observed-URL ledger."""
    client: TavilyClient = field(
        default_factory=lambda: TavilyClient(api_key=config.TAVILY_API_KEY)
    )
    cache: ToolCallCache = field(default_factory=ToolCallCache)
    _count: int = 0
    _cache_hits: int = 0
    _observed_urls: list[str] = field(default_factory=list)

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

    @property
    def observed_urls(self) -> list[str]:
        """URLs the model actually saw across all calls of this instance.
        Used by tasks to record `tool_results_seen` for the eval source check."""
        return list(self._observed_urls)

    def _record_urls(self, urls: list[str]) -> None:
        for u in urls:
            if u and u not in self._observed_urls:
                self._observed_urls.append(u)

    def __call__(self, query: str, days: int | None = None) -> str:
        # Process-wide quota breaker: once Tavily has returned 432 once, every
        # subsequent call short-circuits — no API call, no counter increment.
        # Saves Anthropic tokens on a batch that's now guaranteed to fail
        # downstream (see module docstring).
        if tavily_quota_exhausted():
            return (
                "ERROR: Tavily monthly quota exhausted (HTTP 432). "
                "Top up at tavily.com and rerun. "
                "Produce best-effort output with low confidence."
            )

        # Hard cap (Fix Appendix #10): return a tool error BEFORE incrementing the
        # counter or hitting the network, so the model sees the cap clearly and the
        # account does not silently spin past the budget.
        if self._count >= config.TAVILY_SEARCHES_PER_TASK_CAP:
            return (
                f"ERROR: search cap reached for this task "
                f"({config.TAVILY_SEARCHES_PER_TASK_CAP}). "
                "Produce best-effort output with low confidence."
            )

        # Reject (don't truncate) overlong queries (Fix Appendix #11).
        if len(query) > QUERY_MAX_CHARS:
            return (
                f"ERROR: query too long ({len(query)} chars). "
                f"Rewrite as a concise web search query under {QUERY_MAX_CHARS} "
                "characters and call web_search again."
            )

        self._count += 1
        args: dict[str, Any] = {
            "query": query,
            "backend": "tavily",
            "search_depth": "basic",
            "max_results": 5,
            "tool_version": WEB_SEARCH_TOOL_VERSION,
        }
        if days is not None:
            args["days"] = int(days)

        # Cache lookup first — same query within 24h returns instantly, no API call.
        cached = self.cache.lookup(self.name, args)
        if cached is not None:
            self._cache_hits += 1
            self._record_urls(_extract_urls(cached))
            return cached

        # Cache miss → real Tavily call (rate-limited per service).
        try:
            search_kwargs: dict[str, Any] = {
                "query": query, "search_depth": "basic", "max_results": 5,
            }
            if days is not None:
                # `topic="news"` is what makes Tavily honour the `days` window;
                # otherwise the parameter is silently ignored.
                search_kwargs["topic"] = "news"
                search_kwargs["days"] = int(days)
            with TAVILY_LIMITER:
                response = self.client.search(**search_kwargs)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 432:
                _signal_tavily_quota_exhausted()
                return (
                    "ERROR: Tavily monthly quota exhausted (HTTP 432). "
                    "Top up at tavily.com and rerun. "
                    "Produce best-effort output with low confidence."
                )
            if status == 400:
                # Tavily rejects malformed queries with 400 (e.g. characters
                # the API can't parse, query too long for the topic, weird
                # encoding). Return a structured error rather than propagating
                # — the model can re-issue with a tighter query. Do NOT cache
                # 400 as a real result (different query strings shouldn't
                # share its fate); skip cache.store entirely.
                return (
                    "ERROR: search rejected (HTTP 400 Bad Request). "
                    "The query may be too long, contain unsupported characters, "
                    "or combine incompatible parameters. "
                    "Reformulate as a concise query (under 200 chars, plain "
                    "ASCII, no JSON / parentheses / unusual punctuation) and "
                    "call web_search again."
                )
            if status in (408, 425, 429, 500, 502, 503, 504):
                text = f"ERROR: retryable search failure: {status}"
                # Tiny TTL so a transient blip doesn't poison the cache for 24h.
                self.cache.store(self.name, args, text, ttl_minutes=30)
                return text
            return f"ERROR: search failed: {type(e).__name__}: {e}"
        except (httpx.HTTPError, TimeoutError) as e:
            text = f"ERROR: retryable search failure: {type(e).__name__}"
            self.cache.store(self.name, args, text, ttl_minutes=30)
            return text
        except Exception as e:
            # The Tavily Python SDK currently uses `requests` internally, so
            # status codes surface as requests.exceptions.HTTPError (not
            # httpx). Match by string pattern — brittle but contained, and
            # the alternative (burning the rest of the batch on guaranteed
            # failures) is worse.
            msg = str(e)
            msg_lower = msg.lower()
            if "432" in msg and "tavily" in msg_lower:
                _signal_tavily_quota_exhausted()
                return (
                    "ERROR: Tavily monthly quota exhausted (HTTP 432). "
                    "Top up at tavily.com and rerun. "
                    "Produce best-effort output with low confidence."
                )
            if "400" in msg and ("bad request" in msg_lower or "tavily" in msg_lower):
                return (
                    "ERROR: search rejected (HTTP 400 Bad Request). "
                    "Reformulate as a concise query (under 200 chars, plain "
                    "ASCII, no JSON / parentheses / unusual punctuation) and "
                    "call web_search again."
                )
            # Other unexpected exceptions: bubble so the task records a real failure.
            raise

        results = response.get("results", []) or []
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
        # Successes get the normal 24h TTL.
        self.cache.store(self.name, args, text, ttl_hours=24)
        self._record_urls([r.get("url", "") for r in results])
        return text


def _extract_urls(rendered_text: str) -> list[str]:
    """Pull URLs out of a rendered cache payload. Best-effort: matches `URL: <url>`."""
    urls: list[str] = []
    for line in rendered_text.splitlines():
        if line.startswith("URL: "):
            urls.append(line[len("URL: "):].strip())
    return urls
