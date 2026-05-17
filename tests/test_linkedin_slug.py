"""Unit tests for `tools.linkedin_slug.discover_linkedin_company_url`.

The discovery function is deterministic given a Tavily response, so tests
build the WebSearchTool's rendered-text output by hand and assert on the
SlugDiscovery dataclass returned.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from run_log import ToolCallCache
from tools.linkedin_slug import (
    LOW_CONFIDENCE_THRESHOLD,
    SlugDiscovery,
    _parse_count,
    _parse_tavily_results,
    discover_linkedin_company_url,
)
from tools.web_search import (
    WebSearchTool,
    _reset_tavily_quota_flag_for_tests,
    _signal_tavily_quota_exhausted,
)


@pytest.fixture(autouse=True)
def _reset_breaker():
    _reset_tavily_quota_flag_for_tests()
    yield
    _reset_tavily_quota_flag_for_tests()


def _render(*results: dict[str, str]) -> str:
    """Produce a WebSearchTool-shaped rendered text payload.

    WebSearchTool emits `Result N:\nTitle: ...\nURL: ...\nContent: ...`
    blocks separated by blank lines. Tests build the same shape so the
    discovery parser sees realistic input.
    """
    out = []
    for i, r in enumerate(results, start=1):
        out.append(
            f"Result {i}:\n"
            f"Title: {r.get('title', 'No title')}\n"
            f"URL: {r.get('url', 'No url')}\n"
            f"Content: {r.get('content', 'No content')}"
        )
    return "\n\n".join(out)


def _stub_web_search(rendered: str):
    """Callable that mimics WebSearchTool.__call__ — returns a fixed payload."""
    def _call(query: str, days: int | None = None) -> str:  # noqa: ARG001
        return rendered
    return _call


# ---- Parser sanity ----

def test_parser_unwraps_rendered_results() -> None:
    rendered = _render(
        {"title": "A", "url": "https://www.linkedin.com/company/a/", "content": "x"},
        {"title": "B", "url": "https://www.linkedin.com/company/b/", "content": "y"},
    )
    parsed = _parse_tavily_results(rendered)
    assert len(parsed) == 2
    assert parsed[0]["url"].endswith("/company/a/")
    assert parsed[1]["title"] == "B"


def test_parser_handles_empty_string() -> None:
    assert _parse_tavily_results("") == []


# ---- Count extraction (tiebreak fuel) ----

@pytest.mark.parametrize("blob,expected", [
    ("1,234,567 employees", 1_234_567),
    ("1.2M followers", 1_200_000),
    ("500 employees", 500),
    ("12k followers", 12_000),
    ("no numbers here", 0),
    ("567 employees · 1.5M followers", 1_500_000),
])
def test_parse_count(blob: str, expected: int) -> None:
    assert _parse_count(blob) == expected


# ---- Stripe: short-circuit on exact match + rich snippet ----

def test_stripe_short_circuits_on_exact_match_with_rich_snippet() -> None:
    rendered = _render({
        "title": "Stripe | LinkedIn",
        "url": "https://www.linkedin.com/company/stripe/",
        "content": "Stripe is a financial infrastructure platform. "
                   "8,000+ employees · Headquarters: San Francisco, "
                   "founded 2010 · Industry: Financial Services · Specialties: payments.",
    })
    out = discover_linkedin_company_url("Stripe", None, _stub_web_search(rendered))
    assert out.source == "tavily_exact"
    assert out.url == "https://www.linkedin.com/company/stripe/"
    assert out.confidence >= 0.9


# ---- Miro: scored discovery picks mirohq over the stale /miro/ page ----

def test_miro_picks_mirohq_over_stale_miro_via_signals() -> None:
    rendered = _render(
        {
            "title": "Miro | LinkedIn",
            "url": "https://www.linkedin.com/company/mirohq/",
            "content": "The Innovation Workspace. 1.5M followers · "
                       "2,000+ employees · Headquarters in Amsterdam · "
                       "Founded 2011 · Industry: Software Development · "
                       "Specialties: visual collaboration.",
        },
        {
            "title": "Miro Magazine",
            "url": "https://www.linkedin.com/company/miro/",
            "content": "A small publication. No further info.",
        },
        {
            "title": "Working at Miro",
            "url": "https://www.linkedin.com/company/mirohq/about/",
            "content": "Engineering, design, product careers.",
        },
    )
    out = discover_linkedin_company_url("Miro", "https://www.linkedin.com/company/miro/", _stub_web_search(rendered))
    assert out.source == "tavily_scored"
    assert out.url == "https://www.linkedin.com/company/mirohq/"
    assert out.confidence > 0.5
    assert any("mirohq" in (u or "") for u, _ in out.candidates_scored)


def test_miro_tiebreak_uses_follower_count() -> None:
    """When two candidates score within 15 points, the higher follower count wins."""
    rendered = _render(
        {
            "title": "Miro",
            "url": "https://www.linkedin.com/company/miroai/",
            "content": "Some product. 100 employees · Industry: SaaS · Founded 2024.",
        },
        {
            "title": "Miro",
            "url": "https://www.linkedin.com/company/mirohq/",
            "content": "Visual collaboration. 1.5M followers · 2,000 employees · "
                       "Founded 2011 · Industry: Software · Specialties: design.",
        },
    )
    out = discover_linkedin_company_url("Miro", None, _stub_web_search(rendered))
    assert out.url == "https://www.linkedin.com/company/mirohq/"


# ---- Ambiguous: low confidence flag ----

def test_low_confidence_when_top_candidates_close_and_no_count_signal() -> None:
    """Two regional variants neither exactly matching the brand → close scores
    with no count tiebreak → confidence penalised so the orchestrator can
    route the account to needs_review."""
    rendered = _render(
        {
            "title": "Acme NA | LinkedIn",
            "url": "https://www.linkedin.com/company/acme-north-america/",
            "content": "Industry: Manufacturing · Headquarters: Boston · "
                       "Founded 1985 · Specialties: gadgets.",
        },
        {
            "title": "Acme EU | LinkedIn",
            "url": "https://www.linkedin.com/company/acme-europe/",
            "content": "Industry: Manufacturing · Headquarters: Munich · "
                       "Founded 1990 · Specialties: widgets.",
        },
    )
    out = discover_linkedin_company_url("Acme", None, _stub_web_search(rendered))
    assert out.url is not None
    assert out.source == "tavily_scored", "should not short-circuit on a non-exact slug"
    assert out.confidence < LOW_CONFIDENCE_THRESHOLD + 0.1, (
        f"expected low confidence on ambiguous regional variants, got {out.confidence}"
    )


# ---- Fallback ladder ----

def test_falls_back_to_hint_when_tavily_returns_no_linkedin_urls() -> None:
    rendered = _render({
        "title": "Some random news article",
        "url": "https://example.com/article",
        "content": "No LinkedIn URLs anywhere.",
    })
    out = discover_linkedin_company_url(
        "Acme", "https://www.linkedin.com/company/acme-real/", _stub_web_search(rendered),
    )
    assert out.source == "hint"
    assert out.url == "https://www.linkedin.com/company/acme-real/"


def test_falls_back_to_none_when_no_results_and_no_hint() -> None:
    out = discover_linkedin_company_url("Acme", None, _stub_web_search(""))
    assert out.source == "name_fallback"
    assert out.url is None


def test_tavily_quota_exhausted_skips_search_and_returns_hint() -> None:
    _signal_tavily_quota_exhausted()

    called = []
    def _stub(query: str, days: int | None = None) -> str:  # noqa: ARG001
        called.append(query)
        return "should not be called"

    out = discover_linkedin_company_url(
        "Miro", "https://www.linkedin.com/company/mirohq/", _stub,
    )
    assert out.source == "hint_quota_exhausted"
    assert out.url == "https://www.linkedin.com/company/mirohq/"
    assert called == [], "Tavily must not be called when quota is exhausted"


def test_tavily_returns_error_string_falls_back_to_hint() -> None:
    err = "ERROR: search cap reached for this task (8). Produce best-effort output with low confidence."
    out = discover_linkedin_company_url(
        "Miro", "https://www.linkedin.com/company/mirohq/", _stub_web_search(err),
    )
    assert out.source == "hint"
    assert out.url == "https://www.linkedin.com/company/mirohq/"


def test_tavily_exception_falls_back_to_hint() -> None:
    def _boom(query: str, days: int | None = None) -> str:  # noqa: ARG001
        raise RuntimeError("network down")
    out = discover_linkedin_company_url(
        "Miro", "https://www.linkedin.com/company/mirohq/", _boom,
    )
    assert out.source == "hint"
    assert out.url == "https://www.linkedin.com/company/mirohq/"


# ---- Canonicalization ----

def test_winner_url_is_canonical_form() -> None:
    """No matter what shape Tavily returns, the output URL is `https://www.linkedin.com/company/<slug>/`."""
    rendered = _render({
        "title": "Stripe",
        "url": "http://uk.linkedin.com/company/stripe?trk=foo",
        "content": "employees founded headquarters industry specialties",
    })
    out = discover_linkedin_company_url("Stripe", None, _stub_web_search(rendered))
    assert out.url == "https://www.linkedin.com/company/stripe/"


# ---- Integration with WebSearchTool's actual rendering ----

def test_integration_with_real_websearchtool_render(tmp_path) -> None:
    """Build a real WebSearchTool with a mock Tavily client and verify the
    discovery parser correctly consumes its rendered output."""
    mock_client = MagicMock()
    mock_client.search.return_value = {
        "results": [
            {
                "title": "AlphaSense | LinkedIn",
                "url": "https://www.linkedin.com/company/alphasense/",
                "content": "Market Intelligence. 2,000 employees · "
                           "Headquarters: NYC · Founded 2011 · Industry: Information Services.",
            },
        ],
    }
    cache = ToolCallCache(tmp_path / "runs.db")
    tool = WebSearchTool(client=mock_client, cache=cache)

    out = discover_linkedin_company_url("AlphaSense", None, tool)
    assert out.url == "https://www.linkedin.com/company/alphasense/"
    assert out.source == "tavily_exact"


# ---- Type contract ----

def test_returns_slug_discovery_instance() -> None:
    out = discover_linkedin_company_url("Acme", None, _stub_web_search(""))
    assert isinstance(out, SlugDiscovery)
    assert isinstance(out.candidates_scored, list)
