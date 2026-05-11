"""Tests for module_10 (ad library) — tool + task + prompt schema."""

from __future__ import annotations

from typing import Any

import jsonschema
import pytest

import config
import prompts.module_10_ad_library as prompt
from tasks.module_10 import Module10AdLibrary
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
    task = Module10AdLibrary()
    out: dict[str, Any] = {"platforms": [
        {"platform": "linkedin", "ads_running": 12, "volume": "low",
         "format_mix": [], "note": ""},
    ]}
    assert task.to_fields(out) == {}


def test_task_to_blocks_emits_bullet_per_platform() -> None:
    task = Module10AdLibrary()
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


def test_task_section_targets_creative_posture_ads_running() -> None:
    """Page-body assembly relies on these exact strings — guard them with a test."""
    task = Module10AdLibrary()
    assert task.section == "Creative Posture"
    assert task.subsection == "Ads Running"


def test_task_to_signal_sections_is_empty() -> None:
    """Module 10 doesn't contribute Buying Signals tags."""
    task = Module10AdLibrary()
    assert task.to_signal_sections({"platforms": []}) == []
