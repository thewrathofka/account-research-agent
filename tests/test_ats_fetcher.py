"""Tests for the ATS fetcher tool — title classifier + snapshot diff plumbing.

The Greenhouse HTTP call is mocked so the tests stay offline and deterministic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from tools.ats_fetcher import (
    ATSFetcherTool,
    IMPORTANT_TITLE_PATTERNS,
    _slug_heuristics,
    classify_important_title,
    is_in_scope_location,
)


# ---- title classifier ----

@pytest.mark.parametrize(
    "title,expected_tier",
    [
        ("Marketing AI & Transformation Strategy Lead", "tier_1_marketing_ai"),
        ("AI Marketing Operations Lead", "tier_1_marketing_ai"),
        ("Brand AI Strategist", "tier_1_marketing_ai"),
        ("Director, Product Marketing - International (EMEA & APAC)", "tier_2_senior_leadership"),
        ("Head of Demand Generation", "tier_2_senior_leadership"),
        ("VP of Brand", "tier_2_senior_leadership"),
        ("Chief Marketing Officer", "tier_2_senior_leadership"),
        ("CMO", "tier_2_senior_leadership"),
        ("Creative Director", "tier_2_senior_leadership"),
        ("Art Director", "tier_2_senior_leadership"),
        ("Senior Motion Designer", "tier_3_senior_creative_ic"),
        ("Principal Brand Designer", "tier_3_senior_creative_ic"),
        ("Lead Copywriter", "tier_3_senior_creative_ic"),
        ("Staff Product Designer", "tier_3_senior_creative_ic"),
    ],
)
def test_classifier_positive_cases(title: str, expected_tier: str) -> None:
    result = classify_important_title(title)
    assert result is not None, f"{title!r} should match {expected_tier}"
    assert result[0] == expected_tier, (
        f"{title!r} matched {result[0]}, expected {expected_tier}"
    )


@pytest.mark.parametrize(
    "title",
    [
        # Pure engineering — should not match even with AI in the title.
        "Senior Software Engineer (AI Applications)",
        "Principal AI Platform Engineer",
        "Staff AI Platform Engineer",
        # Non-marketing functions.
        "Senior Backend Engineer",
        "Customer Success Manager",
        "Compliance Analyst",
        "Data Analyst",
        # Mid-level (no senior prefix) creative roles.
        "Marketing Manager",
        "Event Marketing Manager",
        "Marketing Operations Manager",
        "Content Strategist",
        # Non-marketing director.
        "Director of AI Governance, Automation & Analytics",
    ],
)
def test_classifier_negative_cases(title: str) -> None:
    """Titles that should NOT be flagged as important."""
    assert classify_important_title(title) is None, (
        f"{title!r} unexpectedly classified — pattern bank may be too broad"
    )


def test_classifier_first_match_wins() -> None:
    """A title that matches Tier 1 AND Tier 2 should be flagged as Tier 1."""
    title = "Director, Marketing AI Transformation"
    # Tier 1 (marketing+AI) AND Tier 2 (Director... marketing). Tier 1 wins.
    result = classify_important_title(title)
    assert result is not None
    assert result[0] == "tier_1_marketing_ai"


def test_classifier_empty_input() -> None:
    assert classify_important_title("") is None
    assert classify_important_title(None) is None  # type: ignore[arg-type]


def test_classifier_pattern_bank_well_formed() -> None:
    """Each pattern entry has (tier_id, regex, description) shape."""
    for entry in IMPORTANT_TITLE_PATTERNS:
        assert len(entry) == 3
        tier_id, pattern, description = entry
        assert tier_id.startswith("tier_")
        assert hasattr(pattern, "search")
        assert isinstance(description, str) and len(description) > 10


# ---- slug heuristics ----

def test_slug_heuristics_alphasense() -> None:
    """Should produce 'alphasense' as the first guess."""
    candidates = _slug_heuristics("AlphaSense")
    assert candidates[0] == "alphasense"


def test_slug_heuristics_multiword() -> None:
    """'PAR Technology' → try 'partechnology', 'par-technology', 'par'."""
    candidates = _slug_heuristics("PAR Technology")
    assert "partechnology" in candidates
    assert "par-technology" in candidates
    assert "par" in candidates


def test_slug_heuristics_empty() -> None:
    assert _slug_heuristics("") == []


# ---- location classifier ----

@pytest.mark.parametrize(
    "loc,expected",
    [
        # NA
        ("Remote - United States", True),
        ("New York, New York, United States", True),
        ("San Francisco, California, USA", True),
        ("Toronto, Ontario, Canada", True),
        # UK
        ("London, Greater London, England, United Kingdom", True),
        ("Remote - United Kingdom", True),
        ("Edinburgh, Scotland", True),
        # EU
        ("Helsinki, Uusimaa, Finland", True),
        ("Berlin, Germany", True),
        ("Dublin, Ireland", True),
        ("Amsterdam, Netherlands", True),
        # Bare in-scope city
        ("New York", True),
        ("Chicago", True),
        ("London", True),
        ("Berlin", True),
        ("Helsinki", True),
        # Out-of-scope
        ("Bengaluru", False),
        ("Bangalore", False),
        ("Mumbai", False),
        ("Pune", False),
        ("Remote - India", False),
        ("Delhi", False),
        ("Sydney, Australia", False),
        ("Singapore", False),
        ("Tel Aviv, Israel", False),
        ("São Paulo, Brazil", False),
        # Ambiguous / unknown
        ("", None),
        (None, None),
        ("TBD", None),
        ("Remote", None),  # no country qualifier
        # Avoid false positives — "india" substring inside "Indiana" must not
        # flip the location to OUT-OF-SCOPE. (Without a country qualifier we
        # can't confirm in-scope either, so this is correctly None — but the
        # critical thing is it's NOT False.)
        ("Indiana", None),
    ],
)
def test_is_in_scope_location(loc: Any, expected: Any) -> None:
    assert is_in_scope_location(loc) is expected


def test_is_in_scope_multi_segment_in_scope_wins() -> None:
    """A location string like 'Chicago; New York, NY, United States' should be
    in-scope because at least one segment resolves to in-scope."""
    assert is_in_scope_location(
        "Chicago; New York, New York, United States"
    ) is True


# ---- ATSFetcherTool ----

class _FakeResponse:
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status_code = status
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=None, response=None,  # type: ignore[arg-type]
            )

    def json(self) -> dict[str, Any]:
        return self._payload


def _mock_greenhouse_response(jobs: list[dict[str, Any]]):
    """Return a mock httpx.get that always returns `jobs` regardless of URL."""
    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(200, {"jobs": jobs})
    return fake_get


def test_tool_call_surfaces_important_open_roles(tmp_path: Any) -> None:
    from run_log import ATSSnapshotStore
    db = tmp_path / "test.db"
    fake_jobs = [
        {"title": "Marketing AI & Transformation Strategy Lead",
         "absolute_url": "https://example.com/1",
         "location": {"name": "Remote - United States"}},
        {"title": "Senior Backend Engineer",  # should NOT be flagged
         "absolute_url": "https://example.com/2",
         "location": {"name": "New York, New York, United States"}},
        {"title": "Senior Motion Designer",
         "absolute_url": "https://example.com/3",
         "location": {"name": "Remote - United States"}},
    ]
    tool = ATSFetcherTool(snapshot_store=ATSSnapshotStore(path=db))
    with patch("tools.ats_fetcher.httpx.get",
               side_effect=_mock_greenhouse_response(fake_jobs)):
        result = tool(company="TestCo", ats_slug="testco")
    assert "Marketing AI & Transformation Strategy Lead" in result
    assert "Senior Motion Designer" in result
    important_section = result.split("Important roles currently open")[1]
    important_section = important_section.split("Important roles closed")[0]
    assert "[tier_1_marketing_ai]" in important_section
    assert "[tier_3_senior_creative_ic]" in important_section
    assert "Backend Engineer" not in important_section
    # v1.5.0 — in-scope vs out-of-scope counts surfaced in the heading.
    assert "in UK/EU/NA" in important_section
    # Both important roles are Remote-US → IN-SCOPE
    assert "[IN-SCOPE]" in important_section


def test_tool_marks_out_of_scope_roles(tmp_path: Any) -> None:
    """Important roles in India should be flagged OUT-OF-SCOPE so the model
    knows not to count them toward the `hiring` Buying Signal."""
    from run_log import ATSSnapshotStore
    db = tmp_path / "test.db"
    fake_jobs = [
        # US senior designer — IN-SCOPE
        {"title": "Senior Motion Designer",
         "absolute_url": "https://example.com/1",
         "location": {"name": "Remote - United States"}},
        # India senior product designer — OUT-OF-SCOPE
        {"title": "Senior Product Designer",
         "absolute_url": "https://example.com/2",
         "location": {"name": "Bengaluru"}},
        # India tier-1 marketing+AI role — OUT-OF-SCOPE (still important, just
        # doesn't trigger the hiring signal for Superside GTM).
        {"title": "Marketing AI Lead",
         "absolute_url": "https://example.com/3",
         "location": {"name": "Pune"}},
    ]
    tool = ATSFetcherTool(snapshot_store=ATSSnapshotStore(path=db))
    with patch("tools.ats_fetcher.httpx.get",
               side_effect=_mock_greenhouse_response(fake_jobs)):
        result = tool(company="TestCo", ats_slug="testco")
    important_section = result.split("Important roles currently open")[1]
    important_section = important_section.split("Important roles closed")[0]
    # All three are important. One in-scope, two out-of-scope.
    assert "1 in UK/EU/NA" in important_section
    assert "2 elsewhere" in important_section
    # Rule reminder is rendered for the model.
    assert "OUT-OF-SCOPE" in important_section
    assert "do NOT trigger the signal" in important_section


def test_tool_detects_closed_important_roles(tmp_path: Any) -> None:
    """When a previous snapshot exists with an important role no longer present,
    flag it as recently_closed."""
    from run_log import ATSSnapshotStore
    db = tmp_path / "test.db"
    store = ATSSnapshotStore(path=db)
    # Seed a prior snapshot containing an important role that's about to be "closed."
    store.store(
        company="TestCo", provider="greenhouse", slug="testco",
        jobs=[
            {"title": "Marketing AI & Transformation Strategy Lead",
             "absolute_url": "https://example.com/1"},
            {"title": "Head of Brand Marketing", "absolute_url": "https://example.com/2"},
            {"title": "Senior Backend Engineer", "absolute_url": "https://example.com/3"},
        ],
    )
    # Current run: the "Head of Brand Marketing" is gone (closed). Backend Engineer
    # is also gone but should NOT be flagged (not important).
    current_jobs = [
        {"title": "Marketing AI & Transformation Strategy Lead",
         "absolute_url": "https://example.com/1",
         "location": {"name": "Remote"}},
    ]
    tool = ATSFetcherTool(snapshot_store=store)
    with patch("tools.ats_fetcher.httpx.get",
               side_effect=_mock_greenhouse_response(current_jobs)):
        result = tool(company="TestCo", ats_slug="testco")
    closed_section = result.split("Important roles closed since previous snapshot")[1]
    assert "Head of Brand Marketing" in closed_section
    # Backend Engineer was closed but not important — must not appear.
    assert "Backend Engineer" not in closed_section


def test_tool_handles_no_ats_match(tmp_path: Any) -> None:
    """All slug candidates 404 → returns NO_ATS_MATCH instead of raising."""
    from run_log import ATSSnapshotStore
    db = tmp_path / "test.db"
    tool = ATSFetcherTool(snapshot_store=ATSSnapshotStore(path=db))

    def fake_404(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(404, {})

    with patch("tools.ats_fetcher.httpx.get", side_effect=fake_404):
        result = tool(company="NoSuchCo", ats_slug="nosuchco")
    assert "NO_ATS_MATCH" in result
    assert tool.call_count == 1


def test_tool_records_observed_urls(tmp_path: Any) -> None:
    from run_log import ATSSnapshotStore
    db = tmp_path / "test.db"
    fake_jobs = [
        {"title": "Marketing AI Lead", "absolute_url": "https://example.com/a"},
        {"title": "Random Role", "absolute_url": "https://example.com/b"},
    ]
    tool = ATSFetcherTool(snapshot_store=ATSSnapshotStore(path=db))
    with patch("tools.ats_fetcher.httpx.get",
               side_effect=_mock_greenhouse_response(fake_jobs)):
        tool(company="TestCo", ats_slug="testco")
    assert "https://example.com/a" in tool.observed_urls
    assert "https://example.com/b" in tool.observed_urls


def test_tool_rejects_unsupported_provider(tmp_path: Any) -> None:
    from run_log import ATSSnapshotStore
    db = tmp_path / "test.db"
    tool = ATSFetcherTool(snapshot_store=ATSSnapshotStore(path=db))
    result = tool(company="TestCo", provider="lever")
    assert "ERROR: provider='lever' not supported" in result


# ---- ATSSnapshotStore ----

def test_snapshot_store_load_returns_none_on_empty(tmp_path: Any) -> None:
    from run_log import ATSSnapshotStore
    store = ATSSnapshotStore(path=tmp_path / "x.db")
    assert store.load_latest(company="X", provider="greenhouse") is None


def test_snapshot_store_returns_most_recent(tmp_path: Any) -> None:
    from run_log import ATSSnapshotStore
    store = ATSSnapshotStore(path=tmp_path / "x.db")
    store.store(company="X", provider="greenhouse", slug="x",
                jobs=[{"title": "old"}])
    store.store(company="X", provider="greenhouse", slug="x",
                jobs=[{"title": "new"}])
    loaded = store.load_latest(company="X", provider="greenhouse")
    assert loaded is not None
    assert loaded["titles"] == ["new"]


def test_snapshot_store_scopes_by_company_and_provider(tmp_path: Any) -> None:
    from run_log import ATSSnapshotStore
    store = ATSSnapshotStore(path=tmp_path / "x.db")
    store.store(company="A", provider="greenhouse", slug="a", jobs=[{"title": "a-job"}])
    store.store(company="B", provider="greenhouse", slug="b", jobs=[{"title": "b-job"}])
    a = store.load_latest(company="A", provider="greenhouse")
    b = store.load_latest(company="B", provider="greenhouse")
    assert a is not None and a["titles"] == ["a-job"]
    assert b is not None and b["titles"] == ["b-job"]
