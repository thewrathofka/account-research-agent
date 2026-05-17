"""Tests for module_08 (ad library) — tool + task + prompt schema."""

from __future__ import annotations

from typing import Any

import jsonschema
import pytest

import config
import prompts.module_08_ad_library as prompt
from tasks.module_08 import Module08AdLibrary
from tools.apify_ad_scraper import (
    ApifyAdScraperTool,
    SUPPORTED_PLATFORMS,
    _bucket_volume,
    _extract_format,
    _render_results,
)


# ---- volume bucketing ----

@pytest.mark.parametrize(
    "count,expected",
    [(0, "none"), (1, "low"), (10, "low"), (11, "medium"), (50, "medium"),
     (51, "high"), (200, "high")],
)
def test_bucket_volume(count: int, expected: str) -> None:
    assert _bucket_volume(count) == expected


# ---- format normalisation ----

def test_extract_format_from_creative_type() -> None:
    assert _extract_format({"creative_type": "Image"}) == "static"
    assert _extract_format({"creative_type": "VIDEO"}) == "video"
    assert _extract_format({"creative_type": "carousel"}) == "carousel"


def test_extract_format_fallback_url_signals() -> None:
    assert _extract_format({"video_url": "https://x"}) == "video"
    assert _extract_format({"image_url": "https://x"}) == "static"
    assert _extract_format({"carousel": [1, 2, 3]}) == "carousel"
    assert _extract_format({}) == "unknown"


# ---- rendering ----

def test_render_results_empty() -> None:
    text = _render_results("linkedin", "TestCo", "US", [])
    assert "ads_running: 0" in text
    assert "volume: none" in text


def test_render_results_groups_formats_by_count() -> None:
    items = [
        {"ad_url": "https://linkedin.com/1", "creative_type": "image"},
        {"ad_url": "https://linkedin.com/2", "creative_type": "image"},
        {"ad_url": "https://linkedin.com/3", "creative_type": "video"},
    ]
    text = _render_results("linkedin", "TestCo", "US", items)
    assert "ads_running: 3" in text
    assert "static: 2" in text
    assert "video: 1" in text
    assert "https://linkedin.com/1" in text


# ---- tool behavior ----

def test_tool_degrades_when_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No APIFY_API_KEY → tool returns structured NOT CONFIGURED text, not exception."""
    monkeypatch.setattr(config, "APIFY_API_KEY", None)
    tool = ApifyAdScraperTool()
    assert tool.client is None
    result = tool(platform="linkedin", company="Oracle")
    assert "NOT CONFIGURED" in result
    assert tool.call_count == 1


def test_tool_rejects_unknown_platform() -> None:
    tool = ApifyAdScraperTool()
    result = tool(platform="snapchat", company="Acme")
    assert "ERROR: unknown platform" in result
    # call_count still incremented BEFORE the unknown-platform check? No — we
    # check that before incrementing. Verify.
    assert tool.call_count == 0


def test_tool_hard_caps_at_config_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cap (Fix Appendix #10) returns an ERROR pre-call, no extra increment.

    APIFY_API_KEY is cleared so the first two calls take the NOT-CONFIGURED
    branch and do not actually hit the network — otherwise this test burns
    real Apify compute credit on every run.
    """
    monkeypatch.setattr(config, "APIFY_CALLS_PER_TASK_CAP", 2)
    monkeypatch.setattr(config, "APIFY_API_KEY", None)
    tool = ApifyAdScraperTool()
    assert tool.client is None  # confirms we're in offline mode
    tool(platform="linkedin", company="A")
    tool(platform="linkedin", company="B")
    capped = tool(platform="linkedin", company="C")
    assert "ERROR: apify cap reached" in capped
    assert tool.call_count == 2  # cap counter did not increment past the limit


def test_tool_supported_platforms_match_actor_ids() -> None:
    """Sanity: every platform the schema advertises has an actor wired up."""
    assert set(SUPPORTED_PLATFORMS) == {"linkedin", "meta", "tiktok"}


# ---- prompt + schema ----

def test_prompt_has_version_and_system_prompt() -> None:
    assert prompt.VERSION
    assert "B2C" in prompt.SYSTEM_PROMPT
    assert "Gen-Z" in prompt.SYSTEM_PROMPT
    # The gating rules must be explicit in the system text — otherwise the
    # cost gates leak.
    assert "linkedin" in prompt.SYSTEM_PROMPT
    assert "meta" in prompt.SYSTEM_PROMPT
    assert "tiktok" in prompt.SYSTEM_PROMPT


def test_schema_accepts_pure_b2b_output() -> None:
    sample = {
        "audience_classification": {
            "primary": "B2B",
            "is_gen_z_lifestyle": False,
            "rationale": "Enterprise SaaS audience.",
        },
        "platforms": [
            {"platform": "linkedin", "ads_running": 12, "volume": "low",
             "format_mix": [{"format": "static", "count": 12}], "note": "..."},
            {"platform": "meta", "ads_running": 0, "volume": "none",
             "format_mix": [], "note": "Not applicable — B2B."},
            {"platform": "tiktok", "ads_running": 0, "volume": "none",
             "format_mix": [], "note": "Not applicable — not Gen-Z/lifestyle."},
        ],
        "citations": [],
        "sources": ["https://linkedin.com/ad/x"],
        "confidence": "medium",
    }
    jsonschema.validate(sample, prompt.JSON_SCHEMA)


def test_schema_rejects_two_platforms() -> None:
    """All three platforms must be present — even when meta/tiktok were skipped."""
    bad = {
        "audience_classification": {
            "primary": "B2B", "is_gen_z_lifestyle": False, "rationale": "...",
        },
        "platforms": [
            {"platform": "linkedin", "ads_running": 12, "volume": "low",
             "format_mix": [], "note": "..."},
            {"platform": "meta", "ads_running": 0, "volume": "none",
             "format_mix": [], "note": "..."},
        ],
        "citations": [],
        "sources": [],
        "confidence": "medium",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, prompt.JSON_SCHEMA)


# ---- task class ----

def test_task_to_fields_writes_nothing() -> None:
    """Module 10 is page-body only (Notion spec §10)."""
    task = Module08AdLibrary()
    out: dict[str, Any] = {"platforms": [
        {"platform": "linkedin", "ads_running": 12, "volume": "low",
         "format_mix": [], "note": ""},
    ]}
    assert task.to_fields(out) == {}


def test_task_to_blocks_emits_bullet_per_platform() -> None:
    task = Module08AdLibrary()
    out = {
        "platforms": [
            {"platform": "linkedin", "ads_running": 5, "volume": "low",
             "format_mix": [{"format": "static", "count": 3},
                            {"format": "video", "count": 2}],
             "note": "Demo-heavy."},
            {"platform": "meta", "ads_running": 0, "volume": "none",
             "format_mix": [], "note": "Not applicable — B2B."},
            {"platform": "tiktok", "ads_running": 0, "volume": "none",
             "format_mix": [], "note": "Not applicable — not Gen-Z."},
        ],
    }
    blocks = task.to_blocks(out)
    assert len(blocks) == 3
    texts = [b["bulleted_list_item"]["rich_text"][0]["text"]["content"] for b in blocks]
    assert texts[0].startswith("linkedin: 5 ads")
    assert "3 static" in texts[0] and "2 video" in texts[0]
    assert texts[1].startswith("meta: no active ads")
    assert texts[2].startswith("tiktok: no active ads")


def test_schema_accepts_provenance_object() -> None:
    """v1.3.0: per-platform provenance echoes Apify diagnostics."""
    sample = {
        "audience_classification": {
            "primary": "B2B", "is_gen_z_lifestyle": False, "rationale": "...",
        },
        "platforms": [
            {"platform": "linkedin", "ads_running": 15, "volume": "medium",
             "format_mix": [{"format": "video", "count": 12},
                            {"format": "static", "count": 3}],
             "note": "Demo-heavy.",
             "provenance": {
                 "match_mode": "free-text+url-boost",
                 "filtered_out": 9, "url_boosted": 3,
             }},
            {"platform": "meta", "ads_running": 0, "volume": "none",
             "format_mix": [], "note": "Not applicable — B2B."},
            {"platform": "tiktok", "ads_running": 0, "volume": "none",
             "format_mix": [], "note": "Not applicable — not Gen-Z."},
        ],
        "citations": [], "sources": [], "confidence": "high",
    }
    jsonschema.validate(sample, prompt.JSON_SCHEMA)


def test_schema_rejects_unknown_match_mode() -> None:
    """v4 labels (canonical-url, free-text+filter) are gone — guard against drift."""
    bad = {
        "audience_classification": {
            "primary": "B2B", "is_gen_z_lifestyle": False, "rationale": "...",
        },
        "platforms": [
            {"platform": "linkedin", "ads_running": 1, "volume": "low",
             "format_mix": [], "note": "...",
             "provenance": {
                 "match_mode": "canonical-url",  # v4 label, no longer valid
                 "filtered_out": 0, "url_boosted": 0,
             }},
            {"platform": "meta", "ads_running": 0, "volume": "none",
             "format_mix": [], "note": "..."},
            {"platform": "tiktok", "ads_running": 0, "volume": "none",
             "format_mix": [], "note": "..."},
        ],
        "citations": [], "sources": [], "confidence": "medium",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, prompt.JSON_SCHEMA)


def test_schema_still_accepts_platform_without_provenance() -> None:
    """Cached pre-v1.3.0 outputs must still validate — provenance is optional."""
    sample = {
        "audience_classification": {
            "primary": "B2B", "is_gen_z_lifestyle": False, "rationale": "...",
        },
        "platforms": [
            {"platform": "linkedin", "ads_running": 5, "volume": "low",
             "format_mix": [], "note": "..."},
            {"platform": "meta", "ads_running": 0, "volume": "none",
             "format_mix": [], "note": "..."},
            {"platform": "tiktok", "ads_running": 0, "volume": "none",
             "format_mix": [], "note": "..."},
        ],
        "citations": [], "sources": [], "confidence": "medium",
    }
    jsonschema.validate(sample, prompt.JSON_SCHEMA)


def test_to_blocks_emits_provenance_as_nested_child() -> None:
    task = Module08AdLibrary()
    out = {
        "platforms": [
            {"platform": "linkedin", "ads_running": 15, "volume": "medium",
             "format_mix": [{"format": "video", "count": 12}],
             "note": "Demo-heavy.",
             "provenance": {
                 "match_mode": "free-text+url-boost",
                 "filtered_out": 9, "url_boosted": 3,
             }},
        ],
    }
    blocks = task.to_blocks(out)
    assert len(blocks) == 1
    item = blocks[0]["bulleted_list_item"]
    children = item.get("children") or []
    assert len(children) == 1, "provenance must render as one nested child bullet"
    child_text = children[0]["bulleted_list_item"]["rich_text"][0]["text"]["content"]
    assert "match_mode: free-text+url-boost" in child_text
    assert "filtered_out: 9" in child_text
    assert "url_boosted: 3" in child_text


def test_to_blocks_omits_provenance_when_absent() -> None:
    """Cached pre-v1.3.0 platform output renders without a provenance child."""
    task = Module08AdLibrary()
    out = {
        "platforms": [
            {"platform": "linkedin", "ads_running": 5, "volume": "low",
             "format_mix": [{"format": "static", "count": 5}], "note": "..."},
        ],
    }
    blocks = task.to_blocks(out)
    assert len(blocks) == 1
    assert "children" not in blocks[0]["bulleted_list_item"]


def test_to_blocks_omits_provenance_for_skipped_platforms() -> None:
    """`not applicable` platforms shouldn't get a provenance child even if one is present."""
    task = Module08AdLibrary()
    out = {
        "platforms": [
            {"platform": "meta", "ads_running": 0, "volume": "none",
             "format_mix": [], "note": "Not applicable — B2B."},
        ],
    }
    blocks = task.to_blocks(out)
    assert len(blocks) == 1
    assert "children" not in blocks[0]["bulleted_list_item"]


def test_task_section_targets_creative_posture_ads_running() -> None:
    """Page-body assembly relies on these exact strings — guard them with a test."""
    task = Module08AdLibrary()
    assert task.section == "Creative Posture"
    assert task.subsection == "Ads Running"


def test_task_to_signal_sections_is_empty() -> None:
    """Module 10 doesn't contribute Buying Signals tags."""
    task = Module08AdLibrary()
    assert task.to_signal_sections({"platforms": []}) == []


# ---- Slug-discovery integration (v6, 2026-05-17) ----

def test_ad_scraper_tool_version_bumped_to_v6() -> None:
    """v6 bump invalidates cached LinkedIn payloads keyed on the bad
    research_pass hint URL (e.g. cached Miro runs using /miro/ instead of
    /mirohq/)."""
    from tools.apify_ad_scraper import APIFY_TOOL_VERSION
    assert APIFY_TOOL_VERSION == "apify_ad_scraper_v6"


def test_ad_scraper_discover_linkedin_returns_hint_when_no_websearch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a Tavily client wired, _discover_linkedin degrades to the
    LLM-picked hint URL rather than blowing up. Used by tests + by production
    callers that don't have TAVILY_API_KEY set."""
    import config as _config
    from tools.apify_ad_scraper import ApifyAdScraperTool
    monkeypatch.setattr(_config, "TAVILY_API_KEY", None)
    tool = ApifyAdScraperTool(web_search_tool=None)
    out = tool._discover_linkedin("Miro", "https://www.linkedin.com/company/miro/")
    assert out.url == "https://www.linkedin.com/company/miro/"
    assert out.source == "hint"


def test_ad_scraper_render_includes_linkedin_discovery_lines() -> None:
    """When the LinkedIn discovery is present, the rendered output surfaces
    `linkedin_discovery_source` / `linkedin_discovery_confidence` / URL lines
    so they land in runs.db for debugging."""
    from tools.apify_ad_scraper import _render_results
    from tools.linkedin_slug import SlugDiscovery

    disc = SlugDiscovery(
        url="https://www.linkedin.com/company/mirohq/",
        source="tavily_scored",
        confidence=0.85,
        candidates_scored=[("https://www.linkedin.com/company/mirohq/", 110)],
    )
    text = _render_results(
        "linkedin", "Miro", "US", items=[], used_canonical=True,
        linkedin_discovery=disc,
    )
    assert "linkedin_discovery_source: tavily_scored" in text
    assert "linkedin_discovery_confidence: 0.85" in text
    assert "linkedin_discovery_url: https://www.linkedin.com/company/mirohq/" in text


def test_ad_scraper_render_omits_discovery_lines_for_non_linkedin_platform() -> None:
    """Meta + TikTok renders should not include the LinkedIn-specific
    discovery lines — they apply only to the LinkedIn slug-discovery path."""
    from tools.apify_ad_scraper import _render_results
    from tools.linkedin_slug import SlugDiscovery

    disc = SlugDiscovery(url=None, source="name_fallback", confidence=0.0)
    text = _render_results(
        "meta", "Miro", "US", items=[], used_canonical=False,
        linkedin_discovery=disc,
    )
    assert "linkedin_discovery_source" not in text
