"""Tests for canonical-URL plumbing through research_pass → module 10/14 → Apify.

The chain:
  1. research_pass captures linkedin_company_url / facebook_page_url /
     tiktok_handle / greenhouse_slug as nullable schema fields.
  2. Module 10 reads them from context, embeds them in the user message,
     and instructs the model to pass them to apify_ad_scraper.
  3. apify_ad_scraper uses canonical URLs when present (anchors on advertiser),
     falls back to free-text + advertiser-name post-filter otherwise.
  4. Module 14 reads greenhouse_slug and instructs the model to pass it
     verbatim to ats_jobs (skipping the name-derived heuristic).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import jsonschema
import pytest

import prompts.research_pass as research_prompt
from run_log import ToolCallCache
from tasks.module_08 import Module08AdLibrary
from tasks.module_14 import Module14HiringSignal
from tools.apify_ad_scraper import (
    _advertiser_similarity,
    _build_actor_input,
    _extract_advertiser,
    _filter_by_advertiser,
    _tokenize_company_name,
    ApifyAdScraperTool,
)


# ---- research_pass schema accepts canonical-URL fields ----

def _valid_research_pass_sample() -> dict:
    return {
        "company_name": "Stripe, Inc.",
        "company_website": "https://stripe.com/",
        "linkedin_company_url": "https://www.linkedin.com/company/stripe/",
        "facebook_page_url": "https://www.facebook.com/StripePayments",
        "tiktok_handle": None,
        "greenhouse_slug": "stripe",
        "raw_research": "Stripe is a payments company..." + "x" * 100,
        "sources": ["https://stripe.com/about"],
        "confidence": "high",
    }


def test_research_pass_schema_accepts_full_canonical_set() -> None:
    jsonschema.validate(_valid_research_pass_sample(), research_prompt.JSON_SCHEMA)


def test_research_pass_schema_accepts_null_canonical_fields() -> None:
    """Each canonical URL is nullable — model returns null when not findable."""
    sample = _valid_research_pass_sample()
    sample["company_website"] = None
    sample["linkedin_company_url"] = None
    sample["facebook_page_url"] = None
    sample["tiktok_handle"] = None
    sample["greenhouse_slug"] = None
    jsonschema.validate(sample, research_prompt.JSON_SCHEMA)


def test_research_pass_schema_requires_canonical_fields_in_output() -> None:
    """All five must be present in the output (with value or null) so
    downstream tools can rely on the key existing."""
    bad = _valid_research_pass_sample()
    del bad["linkedin_company_url"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, research_prompt.JSON_SCHEMA)


def test_research_pass_prompt_mentions_canonical_fields() -> None:
    """The prompt must instruct the model to extract these without new searches."""
    p = research_prompt.SYSTEM_PROMPT
    for field in ("linkedin_company_url", "facebook_page_url", "tiktok_handle",
                  "greenhouse_slug", "company_website"):
        assert field in p, f"prompt missing instruction for {field}"


# ---- advertiser-name fuzzy match ----

def test_tokenize_lowercases_and_drops_legal_suffixes() -> None:
    assert _tokenize_company_name("AlphaSense, Inc.") == {"alphasense"}
    assert _tokenize_company_name("Stripe Payments, LLC") == {"stripe", "payments"}
    assert _tokenize_company_name("Miro") == {"miro"}
    assert _tokenize_company_name("The Wired Company") == {"wired"}
    assert _tokenize_company_name(None) == set()
    assert _tokenize_company_name("") == set()


def test_similarity_handles_exact_match_with_suffix() -> None:
    """'AlphaSense' should match 'AlphaSense Inc.' at 1.0 — same brand, just
    a legal suffix that the tokenizer drops."""
    assert _advertiser_similarity("AlphaSense, Inc.", "AlphaSense") == 1.0


def test_similarity_rejects_unrelated_brand_sharing_one_token() -> None:
    """'WIRED' (the magazine) vs 'wired-headphones-co' shares ONE token — that's
    Jaccard 1/2 = 0.5. Threshold is 0.5 so 'wired-headphones' would slip
    through, but a more distinct collision like 'WIRED Coffee Roasters'
    (two unique tokens on top of 'wired') gives 1/3 ≈ 0.33 → rejected."""
    assert _advertiser_similarity("WIRED", "WIRED") == 1.0
    # One token in common, plus extra tokens on the candidate side
    sim = _advertiser_similarity("WIRED Coffee Roasters", "WIRED")
    assert sim < 0.5


def test_similarity_handles_empty_or_unknown_advertiser() -> None:
    """Empty/None advertiser → 0.0 similarity. The filter has a separate
    'keep when advertiser-field absent' rule that's tested below."""
    assert _advertiser_similarity(None, "AlphaSense") == 0.0
    assert _advertiser_similarity("", "AlphaSense") == 0.0
    assert _advertiser_similarity("AlphaSense", None) == 0.0


def test_extract_advertiser_prefers_first_present_field() -> None:
    item = {"advertiser": "AlphaSense Inc.", "pageName": "irrelevant"}
    assert _extract_advertiser(item) == "AlphaSense Inc."


def test_extract_advertiser_falls_through_field_list() -> None:
    item = {"pageName": "AlphaSense"}
    assert _extract_advertiser(item) == "AlphaSense"


def test_extract_advertiser_unwraps_nested_dict() -> None:
    """Some Apify actors return advertiser as a dict {name: ..., title: ...}.
    The extractor unwraps it."""
    item = {"advertiser": {"name": "AlphaSense"}}
    assert _extract_advertiser(item) == "AlphaSense"


def test_extract_advertiser_returns_none_when_all_fields_missing() -> None:
    assert _extract_advertiser({"unrelated": "x"}) is None


def test_filter_drops_mismatched_advertisers() -> None:
    """The headline case: WIRED-magazine search returns unrelated ads."""
    items = [
        {"advertiser": "WIRED"},                      # match
        {"advertiser": "WIRED Conde Nast"},           # match (WIRED token)
        {"advertiser": "Wired Magazine"},             # match (different name shape, same brand)
        {"advertiser": "WIRED Coffee Roasters"},      # reject — collision
        {"advertiser": "Bluetooth Wireless Headphones"},  # reject — no match
        {"advertiser": "wired-headphones-co"},        # reject — unrelated
    ]
    kept, dropped, _boosted = _filter_by_advertiser(items, company="WIRED")
    kept_advs = {_extract_advertiser(i) for i in kept}
    assert "WIRED" in kept_advs
    assert "WIRED Coffee Roasters" not in kept_advs
    assert "Bluetooth Wireless Headphones" not in kept_advs
    assert dropped >= 2


def test_filter_keeps_items_without_advertiser_field() -> None:
    """Items lacking any advertiser field are kept — partial signal beats
    zero. Downstream rendering can mark them as 'advertiser unknown'."""
    items = [
        {"adFormat": "video"},                 # no advertiser field — keep
        {"advertiser": "AlphaSense"},          # match — keep
        {"advertiser": "Other Brand Co."},     # no match — drop
    ]
    kept, dropped, _boosted = _filter_by_advertiser(items, company="AlphaSense")
    assert len(kept) == 2
    assert dropped == 1


def test_filter_empty_input_empty_output() -> None:
    kept, dropped, boosted = _filter_by_advertiser([], company="X")
    assert kept == []
    assert dropped == 0
    assert boosted == 0


def test_filter_url_boost_keeps_item_failing_name_match() -> None:
    """v5: when an item's advertiserUrl matches the canonical URL, keep it
    EVEN IF the advertiser name doesn't fuzzy-match. Catches Oracle's case
    where subsidiaries / regional entities advertise under names that share
    few tokens with the brand ('Oracle America Inc' → tokens {oracle,
    america}; name filter alone keeps it, but 'Oracle Public Sector LLC'
    drops below threshold)."""
    items = [
        {
            "advertiser": "Oracle Public Sector LLC",   # 1/3 tokens shared → name filter fails
            "advertiserUrl": "https://www.linkedin.com/company/oracle/",
        },
        {
            "advertiser": "Unrelated Vendor Co.",        # 0 shared tokens, name fails
            "advertiserUrl": "https://www.linkedin.com/company/oracle/",  # URL match → keep
        },
        {
            "advertiser": "Oracle",                      # name PASSES → not boosted
            "advertiserUrl": "https://www.linkedin.com/company/oracle/",
        },
        {
            "advertiser": "Random Brand",                # neither matches → drop
            "advertiserUrl": "https://www.linkedin.com/company/random-brand/",
        },
    ]
    kept, dropped, boosted = _filter_by_advertiser(
        items, company="Oracle",
        canonical_url="https://www.linkedin.com/company/oracle/",
    )
    assert len(kept) == 3
    assert dropped == 1
    # Two items kept ONLY because of URL match (would have failed name filter):
    # Oracle Public Sector LLC + Unrelated Vendor Co.
    assert boosted == 2


def test_filter_url_match_lenient_on_trailing_slash_and_scheme() -> None:
    items = [
        {"advertiser": "X", "advertiserUrl": "linkedin.com/company/oracle"},
        {"advertiser": "X", "advertiserUrl": "https://linkedin.com/company/oracle/"},
        {"advertiser": "X", "advertiserUrl": "https://www.linkedin.com/company/oracle/about?lang=en"},
    ]
    kept, _dropped, _boosted = _filter_by_advertiser(
        items, company="Oracle",
        canonical_url="https://www.linkedin.com/company/oracle/",
    )
    assert len(kept) == 3, "all variants of the canonical URL should match"


# ---- _build_actor_input uses canonical URLs when provided ----

def test_linkedin_always_uses_free_text_v5() -> None:
    """v5: even when canonical URL is provided, searchQuery stays the brand
    name. URL used downstream as filter booster, NOT as query (passing URL
    as query cut Oracle to zero results)."""
    inp = _build_actor_input(
        "linkedin", "AlphaSense", "US", 25,
        linkedin_company_url="https://www.linkedin.com/company/alphasense-inc/",
    )
    assert inp["searchQuery"] == "AlphaSense"


def test_linkedin_free_text_without_url() -> None:
    inp = _build_actor_input("linkedin", "AlphaSense", "US", 25)
    assert inp["searchQuery"] == "AlphaSense"


def test_meta_always_uses_free_text_v5() -> None:
    """v5: same as LinkedIn — Meta's searchTerms always the brand name."""
    inp = _build_actor_input(
        "meta", "Miro", "US", 25,
        facebook_page_url="https://www.facebook.com/miro",
    )
    assert inp["searchTerms"] == ["Miro"]


def test_tiktok_uses_handle_when_provided() -> None:
    inp = _build_actor_input(
        "tiktok", "Miro", "US", 25, tiktok_handle="@miro",
    )
    # The synthesised URL must contain the bare (un-@'d) handle, not the brand.
    url = inp["urls"][0]["url"]
    assert "adv_name=miro" in url
    assert "Miro" not in url.split("?")[1], (
        "url query should not echo the brand name when a handle is present"
    )


def test_tiktok_strips_leading_at_from_handle() -> None:
    inp = _build_actor_input(
        "tiktok", "Miro", "US", 25, tiktok_handle="@miro",
    )
    assert "adv_name=miro" in inp["urls"][0]["url"]


def test_tiktok_falls_back_to_brand_name_without_handle() -> None:
    inp = _build_actor_input("tiktok", "Miro", "US", 25)
    assert "adv_name=Miro" in inp["urls"][0]["url"]


# ---- Tool __call__ threads canonical URLs through end-to-end ----

@pytest.fixture
def apify_tool(tmp_path, monkeypatch):
    # The ApifyAdScraperTool now auto-constructs a real WebSearchTool in
    # __post_init__ when TAVILY_API_KEY is set, then runs slug discovery
    # against Tavily live for every LinkedIn call. Clearing the key keeps
    # the fixture offline + makes the LinkedIn-no-canonical assertion below
    # exercise the genuine fallback path (no Tavily, hint=None → no URL).
    import config
    monkeypatch.setattr(config, "TAVILY_API_KEY", None)
    cache = ToolCallCache(tmp_path / "runs.db")
    client = MagicMock()
    # Mock the actor + dataset chain. Return one item whose advertiser matches
    # so the post-filter keeps it; render_results should show match_mode=canonical-url.
    actor = MagicMock()
    actor.call.return_value = {"defaultDatasetId": "ds-123"}
    client.actor.return_value = actor
    dataset = MagicMock()
    dataset.iterate_items.return_value = iter([
        {"advertiser": "AlphaSense Inc.", "adFormat": "Single Image Ad",
         "detailUrl": "https://www.linkedin.com/ad-library/123"},
    ])
    client.dataset.return_value = dataset
    return ApifyAdScraperTool(client=client, cache=cache), client, actor


def test_tool_renders_url_boost_mode_when_url_passed(apify_tool) -> None:
    """v5: canonical URL drives match_mode='free-text+url-boost' (filter
    received ground truth), but searchQuery stays the brand name."""
    tool, _client, actor = apify_tool
    out = tool(
        platform="linkedin", company="AlphaSense",
        linkedin_company_url="https://www.linkedin.com/company/alphasense-inc/",
    )
    assert "match_mode: free-text+url-boost" in out
    # searchQuery is still the brand name, NOT the URL.
    call_kwargs = actor.call.call_args.kwargs
    assert call_kwargs["run_input"]["searchQuery"] == "AlphaSense"


def test_tool_renders_name_filter_mode_when_no_canonical(apify_tool) -> None:
    tool, _client, _actor = apify_tool
    out = tool(platform="linkedin", company="AlphaSense")
    assert "match_mode: free-text+name-filter" in out


def test_tool_renders_filtered_out_count(tmp_path) -> None:
    """Free-text search returns 2 noise items + 1 match. After filter,
    rendering should show filtered_out: 2 and ads_running: 1."""
    cache = ToolCallCache(tmp_path / "runs.db")
    client = MagicMock()
    actor = MagicMock()
    actor.call.return_value = {"defaultDatasetId": "ds-x"}
    client.actor.return_value = actor
    dataset = MagicMock()
    dataset.iterate_items.return_value = iter([
        {"advertiser": "AlphaSense Inc.", "adFormat": "video"},
        {"advertiser": "Alpha Sense Capital Group", "adFormat": "static"},
        {"advertiser": "Random Wireless Co", "adFormat": "static"},
    ])
    client.dataset.return_value = dataset
    tool = ApifyAdScraperTool(client=client, cache=cache)

    out = tool(platform="linkedin", company="AlphaSense")
    assert "filtered_out: 2" in out
    assert "ads_running: 1" in out


# ---- Module 10's build_user_message surfaces canonical URLs ----

def test_module_10_user_message_includes_canonical_urls_when_present() -> None:
    task = Module08AdLibrary()
    ctx = {
        "research_pass": {
            "raw_research": "Some research text.",
            "linkedin_company_url": "https://www.linkedin.com/company/alphasense-inc/",
            "facebook_page_url": None,
            "tiktok_handle": None,
        },
    }
    msg = task.build_user_message("AlphaSense", ctx)
    assert "linkedin_company_url=https://www.linkedin.com/company/alphasense-inc/" in msg
    # Pass-through instruction must mention the kwarg name
    assert "linkedin_company_url" in msg


def test_module_10_user_message_surfaces_when_no_canonical_urls() -> None:
    """When research_pass didn't find canonical URLs, the user message must
    explicitly warn the model so it weighs confidence accordingly."""
    task = Module08AdLibrary()
    ctx = {
        "research_pass": {
            "raw_research": "Some research text.",
            "linkedin_company_url": None,
            "facebook_page_url": None,
            "tiktok_handle": None,
        },
    }
    msg = task.build_user_message("AlphaSense", ctx)
    assert "NONE" in msg
    assert "free-text" in msg.lower() or "fall back" in msg.lower()


def test_module_10_omits_unknown_kwargs() -> None:
    """If only LinkedIn URL is known, the user message names linkedin only —
    not all three. Keeps the prompt focused."""
    task = Module08AdLibrary()
    ctx = {
        "research_pass": {
            "raw_research": "...",
            "linkedin_company_url": "https://www.linkedin.com/company/x/",
            "facebook_page_url": None,
            "tiktok_handle": None,
        },
    }
    msg = task.build_user_message("X", ctx)
    assert "linkedin_company_url=" in msg
    # The "Canonical platform IDs" block should NOT list the absent ones
    canonical_section_start = msg.index("Canonical platform IDs")
    block = msg[canonical_section_start:canonical_section_start + 400]
    assert "facebook_page_url=" not in block
    assert "tiktok_handle=" not in block


# ---- Module 14 surfaces greenhouse_slug ----

def test_module_14_user_message_uses_research_pass_greenhouse_slug() -> None:
    """When research_pass provided a slug, the user message must instruct the
    model to pass it verbatim instead of letting ats_jobs guess."""
    task = Module14HiringSignal()
    ctx = {
        "module_01_gate": {"regions_present": ["USA"], "operates_in_na": True},
        "research_pass": {"greenhouse_slug": "alphasense"},
    }
    msg = task.build_user_message("AlphaSense", ctx)
    assert "ats_slug='alphasense'" in msg
    assert "confirmed by upstream research" in msg


def test_module_14_user_message_falls_back_when_no_slug() -> None:
    """When research_pass didn't surface a slug, fall back to the legacy
    heuristic-driven instruction (which is what we had before)."""
    task = Module14HiringSignal()
    ctx = {
        "module_01_gate": {"regions_present": ["USA"], "operates_in_na": True},
        "research_pass": {"greenhouse_slug": None},
    }
    msg = task.build_user_message("AlphaSense", ctx)
    assert "ats_slug='alphasense'" not in msg
    # Should still mention ats_jobs and the heuristic fallback path
    assert "ats_jobs" in msg
    assert "heuristic" in msg.lower() or "guess" in msg.lower() or "name" in msg.lower()
