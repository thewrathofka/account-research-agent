"""Tests for the Tavily 432 (quota-exhausted) process-wide circuit breaker.

Once a single 432 trips the flag, subsequent WebSearchTool calls in the same
Python process short-circuit before hitting the network. Saves Anthropic
spend on a batch that's now guaranteed to fail downstream.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

import config
from run_log import ToolCallCache
from tools.web_search import (
    WebSearchTool,
    _reset_tavily_quota_flag_for_tests,
    _signal_tavily_quota_exhausted,
    tavily_quota_exhausted,
)


@pytest.fixture(autouse=True)
def _reset_breaker():
    """Each test starts and ends with a clean breaker flag — otherwise the
    process-wide Event leaks state between unrelated tests."""
    _reset_tavily_quota_flag_for_tests()
    yield
    _reset_tavily_quota_flag_for_tests()


def _make_tool_with_mock_client(tmp_path) -> tuple[WebSearchTool, MagicMock]:
    mock_client = MagicMock()
    cache = ToolCallCache(tmp_path / "runs.db")
    tool = WebSearchTool(client=mock_client, cache=cache)
    return tool, mock_client


def test_flag_starts_unset() -> None:
    assert tavily_quota_exhausted() is False


def test_signal_sets_flag() -> None:
    _signal_tavily_quota_exhausted()
    assert tavily_quota_exhausted() is True


def test_reset_clears_flag() -> None:
    _signal_tavily_quota_exhausted()
    _reset_tavily_quota_flag_for_tests()
    assert tavily_quota_exhausted() is False


def test_tool_short_circuits_when_flag_set(tmp_path) -> None:
    """When the breaker is tripped, __call__ returns an ERROR string WITHOUT
    hitting the API. Verified by the mock client never being called."""
    tool, client = _make_tool_with_mock_client(tmp_path)
    _signal_tavily_quota_exhausted()
    result = tool("anything")
    assert "Tavily monthly quota exhausted" in result
    assert "432" in result
    assert "tavily.com" in result
    assert tool.call_count == 0, "short-circuit must not increment per-task counter"
    client.search.assert_not_called()


def test_real_432_response_trips_breaker(tmp_path) -> None:
    """A real 432 from the Tavily SDK (here simulated via httpx.HTTPStatusError)
    must set the process flag so subsequent calls short-circuit."""
    tool, client = _make_tool_with_mock_client(tmp_path)

    response = httpx.Response(status_code=432, request=httpx.Request("GET", "https://api.tavily.com/search"))
    client.search.side_effect = httpx.HTTPStatusError(
        "432 Plan limit exceeded", request=response.request, response=response,
    )

    # First call propagates 432, returns the structured error, and trips the flag.
    result = tool("first")
    assert "Tavily monthly quota exhausted" in result
    assert tavily_quota_exhausted() is True
    assert client.search.call_count == 1

    # Second call short-circuits — even though the mock would 432 again,
    # we never reach the network.
    result2 = tool("second")
    assert "Tavily monthly quota exhausted" in result2
    assert client.search.call_count == 1, "breaker must prevent the second API call"


def test_requests_style_432_string_match_trips_breaker(tmp_path) -> None:
    """The Tavily Python SDK currently uses `requests` not httpx, so a 432
    bubbles up as a non-httpx exception. The fallback string match must
    recognize it and trip the flag rather than re-raising into the task."""
    tool, client = _make_tool_with_mock_client(tmp_path)

    client.search.side_effect = RuntimeError(
        "HTTPError: 432 Client Error:  for url: https://api.tavily.com/search"
    )

    result = tool("first")
    assert "Tavily monthly quota exhausted" in result
    assert tavily_quota_exhausted() is True


def test_other_4xx_does_not_trip_breaker(tmp_path) -> None:
    """A 404 / 400 / 401 is not a quota issue — those must not set the flag."""
    tool, client = _make_tool_with_mock_client(tmp_path)
    response = httpx.Response(status_code=404, request=httpx.Request("GET", "https://api.tavily.com/search"))
    client.search.side_effect = httpx.HTTPStatusError(
        "404", request=response.request, response=response,
    )

    result = tool("query")
    assert tavily_quota_exhausted() is False
    # Returned the generic "search failed" error, not the quota one.
    assert "quota" not in result.lower()


def test_retryable_4xx_5xx_does_not_trip_breaker(tmp_path) -> None:
    """Retryable statuses (429, 500, 502, 503, 504) are transient — they must
    NOT permanently disable Tavily for the whole batch."""
    tool, client = _make_tool_with_mock_client(tmp_path)
    response = httpx.Response(status_code=429, request=httpx.Request("GET", "https://api.tavily.com/search"))
    client.search.side_effect = httpx.HTTPStatusError(
        "429", request=response.request, response=response,
    )

    result = tool("query")
    assert tavily_quota_exhausted() is False
    assert "retryable" in result.lower()


def test_non_tavily_exception_message_does_not_falsely_trip_breaker(tmp_path) -> None:
    """A random RuntimeError that happens to contain '432' but no 'tavily'
    must NOT trip the breaker — string-match is intentionally conservative."""
    tool, client = _make_tool_with_mock_client(tmp_path)
    client.search.side_effect = RuntimeError("error code 432 from some other API")

    with pytest.raises(RuntimeError):
        tool("query")  # should re-raise, not return ERROR string
    assert tavily_quota_exhausted() is False


def test_cap_check_runs_after_breaker_short_circuit(tmp_path) -> None:
    """Order matters: the quota breaker must take precedence over the per-task
    cap because the cap-reached message says 'low confidence' (try again)
    whereas the quota message says 'top up at tavily.com' (don't retry)."""
    tool, _client = _make_tool_with_mock_client(tmp_path)
    _signal_tavily_quota_exhausted()
    # Bump the per-task counter past the cap, just to be sure both conditions
    # are triggerable at the same time.
    tool._count = config.TAVILY_SEARCHES_PER_TASK_CAP + 1
    result = tool("query")
    # The quota message wins — its priority is intentional.
    assert "quota exhausted" in result.lower()


def test_breaker_does_not_increment_call_counter(tmp_path) -> None:
    """Once the breaker trips, subsequent short-circuited calls must not
    consume from the per-task budget — that budget is for real searches."""
    tool, _client = _make_tool_with_mock_client(tmp_path)
    _signal_tavily_quota_exhausted()
    for _ in range(5):
        tool("anything")
    assert tool.call_count == 0
