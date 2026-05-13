"""Tests for the 2026-05-12 hardening pass:
- _today_header() includes the three news-recency cutoff dates
- _extract_json handles unclosed ```json fences (production failure mode)
- Tavily 400 catch returns structured error rather than propagating
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import httpx
import pytest

from run_log import ToolCallCache
from tasks.base import _extract_json, _today_header
from tools.web_search import (
    WebSearchTool,
    _reset_tavily_quota_flag_for_tests,
)


# ---- _today_header includes cutoff dates ----

def test_today_header_includes_three_cutoff_dates() -> None:
    """Three cutoff dates must be present so the model doesn't do date math."""
    today = date.today()
    expected_12mo = (today - timedelta(days=365)).isoformat()
    expected_6mo = (today - timedelta(days=183)).isoformat()
    expected_90d = (today - timedelta(days=90)).isoformat()
    header = _today_header()
    assert today.isoformat() in header
    assert expected_12mo in header
    assert expected_6mo in header
    assert expected_90d in header


def test_today_header_explains_each_tier() -> None:
    """The three cutoff lines must be labeled so the model knows which to use."""
    header = _today_header()
    assert "12-month" in header
    assert "6-month" in header
    assert "90-day" in header
    # big events / relevant news / buying signals labels
    assert "big events" in header.lower()
    assert "buying signals" in header.lower()


def test_today_header_explicitly_rejects_training_freshness() -> None:
    """The header tells the model not to fall back to its training-data
    sense of 'recent' — that was the root cause of 2024 news leaking in."""
    header = _today_header().lower()
    assert "training" in header
    assert "2024" in header or "out of scope" in header


# ---- _extract_json: open fenced block recovery ----

def test_extract_json_handles_unclosed_fenced_block() -> None:
    """Production failure mode (Roblox research_pass, 2026-05-12): the model
    wrote ```json at the start, then the JSON object, then end_turn without
    a closing ```. The bracket-balanced fallback must recover this."""
    text = """Based on all gathered research, here is the comprehensive output:

```json
{
  "company_name": "Roblox Corporation",
  "confidence": "high"
}

Some trailing prose that should be ignored.
"""
    result = _extract_json(text)
    assert result is not None
    assert result["company_name"] == "Roblox Corporation"
    assert result["confidence"] == "high"


def test_extract_json_closed_fence_still_works() -> None:
    """Don't regress the normal path — closed fences are still preferred."""
    text = """Here is the output:

```json
{"a": 1}
```

Trailing text.
"""
    result = _extract_json(text)
    assert result == {"a": 1}


def test_extract_json_no_fence_bracket_balanced_fallback() -> None:
    """Some models skip the fence entirely — bracket-balanced finds it."""
    text = """Output: {"a": 1, "nested": {"b": 2}}"""
    result = _extract_json(text)
    assert result == {"a": 1, "nested": {"b": 2}}


def test_extract_json_handles_nested_braces_in_strings() -> None:
    """The walker must not get confused by `}` inside a JSON string value."""
    text = """```json
{"description": "Has a } inside the string", "x": 1}
```"""
    result = _extract_json(text)
    assert result == {"description": "Has a } inside the string", "x": 1}


def test_extract_json_returns_none_on_no_json_at_all() -> None:
    """When the model returned pure prose with no JSON, return None so the
    task records a real failure rather than a phantom empty dict."""
    result = _extract_json("Just some text with no JSON object at all.")
    assert result is None


def test_extract_json_handles_open_fence_with_no_closing_brace() -> None:
    """If the model truncated mid-object (no closing `}`), there is no
    recoverable JSON — return None rather than hanging on the walker."""
    text = """```json
{
  "a": 1,
  "b": "no closing brace"""
    result = _extract_json(text)
    assert result is None


# ---- Tavily 400 catch ----

@pytest.fixture(autouse=True)
def _reset_breaker():
    """Each test starts with a clean breaker flag."""
    _reset_tavily_quota_flag_for_tests()
    yield
    _reset_tavily_quota_flag_for_tests()


def _make_tool_with_mock_client(tmp_path) -> tuple[WebSearchTool, MagicMock]:
    mock_client = MagicMock()
    cache = ToolCallCache(tmp_path / "runs.db")
    return WebSearchTool(client=mock_client, cache=cache), mock_client


def test_tavily_400_returns_structured_error(tmp_path) -> None:
    """Tavily 400 = malformed query. Tool must return a structured ERROR
    string the model can read, NOT propagate the exception (which would
    fail the whole task)."""
    tool, client = _make_tool_with_mock_client(tmp_path)
    response = httpx.Response(
        status_code=400,
        request=httpx.Request("GET", "https://api.tavily.com/search"),
    )
    client.search.side_effect = httpx.HTTPStatusError(
        "400 Bad Request", request=response.request, response=response,
    )
    result = tool("some long-ish query with weird (parens) and JSON-like {x: 1}")
    assert "400" in result or "Bad Request" in result
    assert "reformulate" in result.lower() or "concise" in result.lower()
    # Caller should not be receiving the raw exception object.
    assert "HTTPStatusError" not in result


def test_tavily_400_does_not_trip_quota_breaker(tmp_path) -> None:
    """400 is a query-side issue, not quota — breaker must stay off."""
    from tools.web_search import tavily_quota_exhausted
    tool, client = _make_tool_with_mock_client(tmp_path)
    response = httpx.Response(
        status_code=400,
        request=httpx.Request("GET", "https://api.tavily.com/search"),
    )
    client.search.side_effect = httpx.HTTPStatusError(
        "400", request=response.request, response=response,
    )
    tool("query")
    assert tavily_quota_exhausted() is False


def test_tavily_400_via_requests_style_exception(tmp_path) -> None:
    """When the underlying Tavily SDK raises a requests.HTTPError instead of
    httpx, the string-match fallback should still recognize 400 and return
    a structured error."""
    tool, client = _make_tool_with_mock_client(tmp_path)
    client.search.side_effect = RuntimeError(
        "HTTPError: 400 Client Error: Bad Request for url: https://api.tavily.com/search"
    )
    result = tool("query")
    assert "400" in result or "Bad Request" in result
    assert "reformulate" in result.lower() or "concise" in result.lower()


def test_tavily_400_does_not_cache_response(tmp_path) -> None:
    """400 is per-query — a future search with a tighter query should hit
    the API again, not get a stale 'rejected' from cache."""
    tool, client = _make_tool_with_mock_client(tmp_path)
    response = httpx.Response(
        status_code=400,
        request=httpx.Request("GET", "https://api.tavily.com/search"),
    )
    client.search.side_effect = httpx.HTTPStatusError(
        "400", request=response.request, response=response,
    )
    # First call returns the structured 400.
    tool("bad query")
    # If the response were cached, a SECOND call with the same args would
    # short-circuit before the mock was invoked. We assert the mock IS
    # invoked again — proves the 400 didn't get cached.
    assert client.search.call_count == 1
    tool("bad query")
    assert client.search.call_count == 2


def test_tavily_other_4xx_still_propagates_or_returns_search_failed(tmp_path) -> None:
    """A 401 / 403 is auth-side and should NOT be silently swallowed as a
    queryable error. The existing path returns 'search failed: ...' which
    is correct."""
    tool, client = _make_tool_with_mock_client(tmp_path)
    response = httpx.Response(
        status_code=403,
        request=httpx.Request("GET", "https://api.tavily.com/search"),
    )
    client.search.side_effect = httpx.HTTPStatusError(
        "403", request=response.request, response=response,
    )
    result = tool("query")
    assert "search failed" in result.lower() or "403" in result
