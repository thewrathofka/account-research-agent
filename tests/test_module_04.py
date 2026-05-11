"""Tests for module_04 (pain-point synthesis) — prompt schema + task hooks."""

from __future__ import annotations

import jsonschema
import pytest

import prompts.module_04_pain_points as prompt
from tasks.module_04 import Module04PainPoints, UPSTREAM_MODULES


# ---- prompt + schema ----

def test_pain_types_vocabulary_complete() -> None:
    """The 6 canonical pain types must match the Superside value-prop spec."""
    assert set(prompt.PAIN_TYPES) == {
        "production_bottleneck",
        "agency_cost_burn",
        "hiring_gap",
        "multi_market_localization",
        "post_layoff_pressure",
        "ai_creative_receptivity",
    }


def test_schema_accepts_valid_two_pain_output() -> None:
    sample = {
        "pain_points": [
            {
                "pain_type": "post_layoff_pressure",
                "hypothesis": "Oracle's 18% layoff overlaps with 24 active ads...",
                "grounding": [
                    {"module": "module_14_hiring_signal", "datum": "downsizing"},
                    {"module": "module_10_ad_library", "datum": "24 LinkedIn ads"},
                ],
                "superside_angle": "Post-launch ABM surge",
                "confidence": "high",
            },
            {
                "pain_type": "multi_market_localization",
                "hypothesis": "Oracle operates EU + NA + global...",
                "grounding": [
                    {"module": "module_01_gate", "datum": "regions_present=USA, UK, DE"},
                ],
                "superside_angle": "Multi-market localization",
                "confidence": "medium",
            },
        ],
        "sources": ["https://example.com/oracle"],
        "confidence": "medium",
    }
    jsonschema.validate(sample, prompt.JSON_SCHEMA)


def test_schema_rejects_unknown_pain_type() -> None:
    bad = {
        "pain_points": [
            {
                "pain_type": "vibes_based_pain",   # not in PAIN_TYPES enum
                "hypothesis": "...",
                "grounding": [{"module": "x", "datum": "y"}],
                "superside_angle": "...",
                "confidence": "medium",
            },
        ],
        "sources": [],
        "confidence": "medium",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, prompt.JSON_SCHEMA)


def test_schema_requires_at_least_one_grounding_per_pain() -> None:
    """Empty grounding = ungrounded horoscope. Must be rejected."""
    bad = {
        "pain_points": [
            {
                "pain_type": "production_bottleneck",
                "hypothesis": "they probably need more creative",
                "grounding": [],
                "superside_angle": "creative production",
                "confidence": "low",
            },
        ],
        "sources": [],
        "confidence": "low",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, prompt.JSON_SCHEMA)


def test_schema_accepts_zero_pain_points() -> None:
    """When upstream context is thin, returning 0 pains is the right answer."""
    sample = {
        "pain_points": [],
        "sources": [],
        "confidence": "low",
    }
    jsonschema.validate(sample, prompt.JSON_SCHEMA)


def test_schema_caps_at_four_pain_points() -> None:
    too_many = {
        "pain_points": [
            {
                "pain_type": "production_bottleneck",
                "hypothesis": f"hyp {i}",
                "grounding": [{"module": "x", "datum": f"d{i}"}],
                "superside_angle": "x",
                "confidence": "low",
            }
            for i in range(5)
        ],
        "sources": [],
        "confidence": "low",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(too_many, prompt.JSON_SCHEMA)


def test_prompt_metadata() -> None:
    assert prompt.VERSION
    assert "Superside" in prompt.SYSTEM_PROMPT
    # The grounding requirement must be explicit in the system prompt or the
    # eval can't enforce it.
    assert "grounding" in prompt.SYSTEM_PROMPT.lower()
    # The six canonical pain types must each appear in the prompt body.
    for pt in prompt.PAIN_TYPES:
        assert pt in prompt.SYSTEM_PROMPT


# ---- task class ----

def test_task_synthesis_only() -> None:
    task = Module04PainPoints()
    assert task.synthesis_only is True
    assert task.build_tools() == []


def test_task_section_targets_possible_pain_points() -> None:
    task = Module04PainPoints()
    assert task.section == "Possible Pain Points"
    assert task.subsection is None


def test_task_to_fields_empty() -> None:
    """Module 4 is page-body only (spec §4)."""
    task = Module04PainPoints()
    assert task.to_fields({"pain_points": [{"pain_type": "x"}]}) == {}


def test_task_to_blocks_emits_lead_plus_grounding() -> None:
    task = Module04PainPoints()
    out = {
        "pain_points": [
            {
                "pain_type": "post_layoff_pressure",
                "hypothesis": "Oracle just cut 25k — surge capacity is the bottleneck.",
                "grounding": [
                    {"module": "module_14_hiring_signal", "datum": "downsizing, 25k cut"},
                    {"module": "module_10_ad_library", "datum": "24 ads, 83% video"},
                ],
                "superside_angle": "Post-layoff overflow",
                "confidence": "high",
            },
        ],
        "sources": [],
        "confidence": "medium",
    }
    blocks = task.to_blocks(out)
    # 1 lead bullet + 2 grounding bullets = 3
    assert len(blocks) == 3
    lead = blocks[0]["bulleted_list_item"]["rich_text"][0]["text"]["content"]
    assert lead.startswith("post_layoff_pressure")
    assert "Superside angle" in lead
    g0 = blocks[1]["bulleted_list_item"]["rich_text"][0]["text"]["content"]
    assert "module_14_hiring_signal" in g0


def test_task_to_blocks_empty_pains_returns_explainer() -> None:
    task = Module04PainPoints()
    blocks = task.to_blocks({"pain_points": [], "sources": [], "confidence": "low"})
    assert len(blocks) == 1
    # The empty-state paragraph should hint at why (sufficient grounding lacking).
    text = blocks[0]["paragraph"]["rich_text"][0]["text"]["content"]
    assert "grounding" in text or "pain" in text.lower()


def test_task_to_signal_sections_empty() -> None:
    """Module 4 doesn't contribute Buying Signals tags."""
    task = Module04PainPoints()
    assert task.to_signal_sections({"pain_points": []}) == []


def test_build_user_message_pulls_upstream_outputs() -> None:
    """The synthesis prompt must include each upstream module's output JSON."""
    task = Module04PainPoints()
    fake_context = {
        "module_01_gate": {
            "size_band": "5000+", "regions_present": ["USA", "UK"],
            "sources": ["https://gate.example.com"],
        },
        "module_14_hiring_signal": {
            "headcount_signal": "downsizing",
            "sources": ["https://layoffs.example.com"],
        },
    }
    msg = task.build_user_message("Oracle", fake_context)
    # Every declared upstream module appears as a section header (present or absent).
    for mod in UPSTREAM_MODULES:
        assert f"### {mod}" in msg
    # The two we DID provide have actual JSON; the others are "(not run...)".
    assert "regions_present" in msg
    assert "downsizing" in msg
    assert "(not run or no output)" in msg  # for the modules NOT in context
    # Sources block is present and unioned.
    assert "https://gate.example.com" in msg
    assert "https://layoffs.example.com" in msg


def test_build_user_message_handles_empty_context() -> None:
    """Synthesis prompt must still build (degraded) when upstream context is empty."""
    task = Module04PainPoints()
    msg = task.build_user_message("Oracle", {})
    assert "Oracle" in msg
    assert "(not run or no output)" in msg
    assert "(no sources captured)" in msg


# ---- registry wiring ----

def test_task_registered_in_phase2_after_dependencies() -> None:
    """Module 04 must be LAST in PHASE2_TASKS — all its upstreams have to run first."""
    from tasks import PHASE2_TASKS
    assert PHASE2_TASKS[-1] == "module_04_pain_points"
    # And each of its upstream deps must appear earlier than it does.
    pos_04 = PHASE2_TASKS.index("module_04_pain_points")
    for upstream in UPSTREAM_MODULES:
        assert upstream in PHASE2_TASKS, f"{upstream} missing from PHASE2_TASKS"
        assert PHASE2_TASKS.index(upstream) < pos_04, (
            f"{upstream} runs AFTER module_04 — context envelope won't have its output"
        )
