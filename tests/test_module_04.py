"""Tests for module_04 (strategic narrative + pain tags) — v2.0.0."""

from __future__ import annotations

import jsonschema
import pytest

import crm
import prompts.module_04_pain_points as prompt
from tasks.module_04 import ANCHOR_MODULES, Module04PainPoints


# ---- vocabulary ----

def test_tag_vocab_matches_crm_options() -> None:
    """The prompt's TAG_VOCAB must align with the validated CRM option set —
    if they drift, the model could emit tags the writeback layer silently drops."""
    assert set(prompt.TAG_VOCAB.keys()) == crm.PAIN_POINT_TAG_OPTIONS


def test_tag_vocab_has_kalis_tbauction_examples() -> None:
    """The four tags Kali named in the design discussion (TBAuction example)
    must all exist — they're the minimum baseline."""
    for required in ["creative production", "localization", "new territory", "strategy"]:
        assert required in prompt.TAG_VOCAB


# ---- prompt + schema ----

def test_prompt_metadata() -> None:
    assert prompt.VERSION.startswith("v2.")
    # System prompt must forbid the v1.x failure modes.
    sys_text = prompt.SYSTEM_PROMPT.lower()
    assert "cold-email" in sys_text or "cold email" in sys_text
    assert "module" in sys_text  # context of "do not cite module names"
    assert "horoscope" in sys_text


def test_schema_accepts_valid_narrative_output() -> None:
    sample = {
        "narrative": (
            "TBAuction operates an auction marketplace in a category dominated "
            "by Meta Marketplace [1]. Their conversion play is teaching sellers "
            "in specific categories that auctions outperform 'list it on "
            "Marketplace' for high-value goods [2].\n\nThey are expanding into "
            "Italy [3], compounding the creative challenge — education-first "
            "creative across product demos, comparison framing, and seller "
            "success stories, localized for Italian sellers, while keeping "
            "home-market demand-gen alive."
        ),
        "tags": ["audience education", "competitive displacement", "localization",
                 "new territory", "creative production"],
        "citations": [
            {"n": 1, "title": "tbauction.com — about", "url": "https://www.tbauction.com/about"},
            {"n": 2, "title": "techcrunch.com — auction platforms", "url": "https://techcrunch.com/x"},
            {"n": 3, "title": "reuters.com — Italy launch", "url": "https://reuters.com/y"},
        ],
        "sources": ["https://www.tbauction.com/about", "https://techcrunch.com/x",
                    "https://reuters.com/y"],
        "confidence": "high",
    }
    jsonschema.validate(sample, prompt.JSON_SCHEMA)


def test_schema_rejects_unknown_tag() -> None:
    bad = {
        "narrative": "x" * 60,
        "tags": ["audience education", "vibes_based_tag"],  # second is not in enum
        "citations": [],
        "sources": [],
        "confidence": "medium",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, prompt.JSON_SCHEMA)


def test_schema_rejects_empty_narrative() -> None:
    bad = {
        "narrative": "",
        "tags": ["strategy"],
        "citations": [],
        "sources": [],
        "confidence": "low",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, prompt.JSON_SCHEMA)


def test_schema_rejects_too_many_tags() -> None:
    too_many = {
        "narrative": "x" * 60,
        "tags": ["creative production", "localization", "new territory",
                 "strategy", "audience education", "competitive displacement"],
        "citations": [],
        "sources": [],
        "confidence": "high",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(too_many, prompt.JSON_SCHEMA)


def test_schema_accepts_zero_tags() -> None:
    """Thin research → narrative-only output should still validate."""
    sample = {
        "narrative": "x" * 60,
        "tags": [],
        "citations": [],
        "sources": [],
        "confidence": "low",
    }
    jsonschema.validate(sample, prompt.JSON_SCHEMA)


def test_schema_rejects_citation_missing_url() -> None:
    bad = {
        "narrative": "x" * 60,
        "tags": [],
        "citations": [{"n": 1, "title": "no url here"}],  # url required
        "sources": [],
        "confidence": "low",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, prompt.JSON_SCHEMA)


# ---- task class ----

def test_task_synthesis_only() -> None:
    task = Module04PainPoints()
    assert task.synthesis_only is True
    assert task.build_tools() == []


def test_task_section_targets_possible_pain_points() -> None:
    task = Module04PainPoints()
    assert task.section == "Possible Pain Points"
    assert task.subsection is None


def test_task_to_fields_writes_pain_point_tags() -> None:
    task = Module04PainPoints()
    out = {
        "tags": ["audience education", "localization", "new territory"],
    }
    fields = task.to_fields(out)
    assert crm.PROP_PAIN_POINT_TAGS in fields
    names = [item["name"] for item in fields[crm.PROP_PAIN_POINT_TAGS]["multi_select"]]
    assert names == ["audience education", "localization", "new territory"]


def test_task_to_fields_filters_unknown_tags() -> None:
    """Stray labels from the model are dropped, not exception-raised."""
    task = Module04PainPoints()
    out = {"tags": ["localization", "vibes_based_tag", "creative production"]}
    fields = task.to_fields(out)
    names = [item["name"] for item in fields[crm.PROP_PAIN_POINT_TAGS]["multi_select"]]
    assert names == ["localization", "creative production"]  # vibes_based_tag dropped


def test_task_to_fields_emits_empty_multiselect_when_no_tags() -> None:
    """Empty multi_select overwrites stale tags from prior runs (same contract
    as Buying Signals)."""
    task = Module04PainPoints()
    fields = task.to_fields({"tags": []})
    assert fields == {crm.PROP_PAIN_POINT_TAGS: {"multi_select": []}}


def test_task_to_blocks_emits_paragraphs_then_tag_bullet() -> None:
    task = Module04PainPoints()
    out = {
        "narrative": "Paragraph one about positioning.\n\nParagraph two about growth.",
        "tags": ["strategy", "new territory"],
        "citations": [],
    }
    blocks = task.to_blocks(out)
    # 2 paragraphs + 1 tag bullet = 3 (no citations → no footnote section)
    assert len(blocks) == 3
    assert blocks[0]["type"] == "paragraph"
    assert blocks[1]["type"] == "paragraph"
    assert blocks[2]["type"] == "bulleted_list_item"
    p1 = blocks[0]["paragraph"]["rich_text"][0]["text"]["content"]
    p2 = blocks[1]["paragraph"]["rich_text"][0]["text"]["content"]
    assert p1.startswith("Paragraph one")
    assert p2.startswith("Paragraph two")
    tag_line = blocks[2]["bulleted_list_item"]["rich_text"][0]["text"]["content"]
    assert tag_line.startswith("Pain tags:")
    assert "strategy" in tag_line and "new territory" in tag_line


def test_to_blocks_keeps_citation_markers_as_plain_text() -> None:
    """Module 4 emits `[N]` markers as plain text — the orchestrator handles
    renumbering + link rendering per-section. The task itself stays simple."""
    task = Module04PainPoints()
    out = {
        "narrative": "Oracle is shifting toward AI infrastructure [1]. Their conversion play targets hyperscaler clients [2].",
        "tags": [],
        "citations": [
            {"n": 1, "title": "cnbc.com — Q3 FY26", "url": "https://www.cnbc.com/oracle-q3"},
            {"n": 2, "title": "axios.com — OCI AI", "url": "https://www.axios.com/oci-ai"},
        ],
    }
    blocks = task.to_blocks(out)
    # Just the narrative paragraph — no per-module footnote (orchestrator owns it).
    assert len(blocks) == 1
    para_rich = blocks[0]["paragraph"]["rich_text"]
    assert len(para_rich) == 1, "to_blocks emits plain text; the orchestrator splits + links"
    content = para_rich[0]["text"]["content"]
    assert "[1]" in content and "[2]" in content
    assert "link" not in para_rich[0]["text"]


def test_to_blocks_omits_footnote_bullets_module_side() -> None:
    """Module 4 must not emit its own footnote bullets — the orchestrator
    handles per-section citation footnotes, so duplicating here would render
    the bullets twice on the page."""
    task = Module04PainPoints()
    out = {
        "narrative": "Claim with [1] and [2].",
        "tags": [],
        "citations": [
            {"n": 1, "title": "a", "url": "https://example.com/a"},
            {"n": 2, "title": "b", "url": "https://example.com/b"},
        ],
    }
    blocks = task.to_blocks(out)
    types = [b["type"] for b in blocks]
    # Should be: just the narrative paragraph. No footnote-style bullets.
    assert types == ["paragraph"]


def test_task_to_blocks_drops_unknown_tags_from_bullet() -> None:
    task = Module04PainPoints()
    out = {
        "narrative": "x" * 60,
        "tags": ["localization", "vibes_based_tag"],
    }
    blocks = task.to_blocks(out)
    tag_line = blocks[-1]["bulleted_list_item"]["rich_text"][0]["text"]["content"]
    assert "vibes_based_tag" not in tag_line
    assert "localization" in tag_line


def test_task_to_blocks_omits_tag_bullet_when_no_valid_tags() -> None:
    task = Module04PainPoints()
    out = {"narrative": "x" * 60, "tags": []}
    blocks = task.to_blocks(out)
    types = [b["type"] for b in blocks]
    assert "bulleted_list_item" not in types


def test_task_to_blocks_empty_narrative_returns_explainer() -> None:
    task = Module04PainPoints()
    blocks = task.to_blocks({"narrative": "", "tags": []})
    assert len(blocks) == 1
    text = blocks[0]["paragraph"]["rich_text"][0]["text"]["content"]
    assert "research" in text.lower() or "narrative" in text.lower()


def test_task_to_signal_sections_empty() -> None:
    """Module 4 doesn't contribute Buying Signals tags."""
    task = Module04PainPoints()
    assert task.to_signal_sections({"narrative": "x", "tags": []}) == []


# ---- build_user_message ----

def test_build_user_message_includes_raw_research_and_anchors() -> None:
    task = Module04PainPoints()
    fake_context = {
        "research_pass": {
            "raw_research": "TBAuction is an auction platform competing with Meta Marketplace...",
            "sources": ["https://tba.example.com"],
        },
        "module_01_gate": {
            "size_band": "1000-2000", "regions_present": ["Netherlands", "Italy"],
            "sources": ["https://gate.example.com"],
        },
        "module_03_revenue_model": {
            "revenue_model": "Transaction fees on auctions",
            "primary_customer_segment": "Individual + small-business sellers",
            "sources": [],
        },
        "module_12_competitor_snapshot": {
            "competitors": [{"name": "Meta Marketplace"}, {"name": "eBay"}],
            "sources": [],
        },
    }
    msg = task.build_user_message("TBAuction", fake_context)
    # raw_research is the primary narrative source
    assert "TBAuction is an auction platform" in msg
    # All three anchor modules render their JSON
    for mod in ANCHOR_MODULES:
        assert f"### {mod}" in msg
    assert "Netherlands" in msg
    assert "Meta Marketplace" in msg
    # Sources are unioned, raw_research sources included
    assert "https://tba.example.com" in msg
    assert "https://gate.example.com" in msg


def test_build_user_message_excludes_mechanical_modules() -> None:
    """The mechanical signal modules (06/07/09/10/14) must NOT appear in the
    prompt — that's the whole point of the v2.0.0 redesign."""
    task = Module04PainPoints()
    fake_context = {
        "research_pass": {"raw_research": "x", "sources": []},
        "module_01_gate": {"regions_present": ["USA"]},
        # Even if these are in the envelope, the prompt must not embed them:
        "module_06_structural_news": {"structure_note": "mass layoffs"},
        "module_07_trigger_events": {"triggers_detected": ["funding round"]},
        "module_09_creative_reality": {"creative_posture_summary": "..."},
        "module_10_ad_library": {"platforms": [{"platform": "linkedin"}]},
        "module_14_hiring_signal": {"headcount_signal": "downsizing"},
    }
    msg = task.build_user_message("X", fake_context)
    for mechanical in ("module_06_structural_news", "module_07_trigger_events",
                       "module_09_creative_reality", "module_10_ad_library",
                       "module_14_hiring_signal"):
        assert f"### {mechanical}" not in msg, (
            f"Mechanical module {mechanical} leaked into module 4 prompt — "
            "v2.0.0 design explicitly excludes these."
        )


def test_build_user_message_handles_missing_research_pass() -> None:
    task = Module04PainPoints()
    msg = task.build_user_message("X", {})
    # Should still build (degraded path)
    assert "X" in msg
    assert "(no research_pass output available" in msg


def test_build_user_message_handles_missing_anchors() -> None:
    task = Module04PainPoints()
    msg = task.build_user_message("X", {"research_pass": {"raw_research": "...", "sources": []}})
    # All anchor modules render as "(not run or no output)" when absent
    for mod in ANCHOR_MODULES:
        assert f"### {mod}" in msg
    assert "(not run or no output)" in msg


# ---- registry wiring ----

def test_task_registered_in_phase2_after_dependencies() -> None:
    """Module 04 must be LAST in PHASE2_TASKS, and the 3 anchor deps must
    appear earlier so the context envelope has their outputs."""
    from tasks import PHASE2_TASKS
    assert PHASE2_TASKS[-1] == "module_04_pain_points"
    pos_04 = PHASE2_TASKS.index("module_04_pain_points")
    for anchor in ANCHOR_MODULES:
        assert anchor in PHASE2_TASKS, f"{anchor} missing from PHASE2_TASKS"
        assert PHASE2_TASKS.index(anchor) < pos_04, (
            f"{anchor} runs AFTER module_04 — context envelope won't have its output"
        )


def test_anchor_modules_excludes_mechanical_signal_modules() -> None:
    """v2.0.0 contract: only the 3 light anchors, no mechanical signals."""
    assert set(ANCHOR_MODULES) == {
        "module_01_gate",
        "module_03_revenue_model",
        "module_12_competitor_snapshot",
    }
