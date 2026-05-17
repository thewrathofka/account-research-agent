"""Tests for Phase B — per-task diff detection (detect_events).

Each task class that overrides detect_events gets its own test block with
hand-crafted (prev, curr) output pairs. The first-run case (prev=None) and
the prompt-version mismatch case are covered at the orchestrator integration
level in tests/test_freshness_gate.py / this file's orchestrator block.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from crm import Account
from orchestrator import Orchestrator
from providers.base import ProviderResult
from run_log import RunLog, RunRecord
from tasks.base import DetectedEvent
from tasks.module_03 import Module03PainPoints
from tasks.module_04 import Module04CorporateStructure
from tasks.module_05 import Module05StructuralNews
from tasks.module_06 import Module06TriggerEvents
from tasks.module_11 import Module11HiringSignal


# ---- Module 05 (structural news; was module_06 pre-rename) ----

def test_module_05_alerts_on_bankruptcy_transition():
    task = Module05StructuralNews()
    prev = {"structure_note": None, "buying_implication": None, "confidence": "high"}
    curr = {
        "structure_note": "bankruptcy",
        "event_summary": "Chapter 7 filing",
        "event_date": "2026-05-10",
        "buying_implication": "buying-frozen",
        "confidence": "high",
        "sources": ["https://example.com/news"],
    }
    events = task.detect_events(prev, curr)
    # Two events: one for the structure_note transition, one for the
    # buying_implication going to buying-frozen. (Both stand on their own.)
    signals = {e.signal_type for e in events}
    assert "bankruptcy" in signals
    # Bankruptcy already flips the implication, so buying-frozen also fires.
    assert "structure-ambiguous" in signals


def test_module_05_no_alert_on_same_state():
    task = Module05StructuralNews()
    prev = {"structure_note": "mass layoffs", "event_date": "2026-04-01", "confidence": "medium"}
    curr = {"structure_note": "mass layoffs", "event_date": "2026-04-01", "confidence": "medium"}
    assert task.detect_events(prev, curr) == []


def test_module_05_alerts_on_second_wave_with_later_date():
    """Same note, new event_date >=1 month later → second-wave alert."""
    task = Module05StructuralNews()
    prev = {"structure_note": "mass layoffs", "event_date": "2026-01-15", "confidence": "medium"}
    curr = {"structure_note": "mass layoffs", "event_date": "2026-04-20", "confidence": "medium"}
    events = task.detect_events(prev, curr)
    assert any(e.signal_type == "layoffs" for e in events)
    assert any("second" in e.summary.lower() or "second wave" in e.summary.lower() for e in events)


def test_module_05_alerts_on_confidence_regression():
    task = Module05StructuralNews()
    prev = {"structure_note": "recent IPO", "event_date": "2025-11-01", "confidence": "high"}
    curr = {"structure_note": "recent IPO", "event_date": "2025-11-01", "confidence": "low"}
    events = task.detect_events(prev, curr)
    assert any(e.signal_type == "structure-ambiguous" for e in events)
    assert any("confidence" in e.summary.lower() for e in events)


def test_module_05_first_run_returns_empty():
    """No prior_output → no events (alerts trigger on TRANSITIONS only)."""
    task = Module05StructuralNews()
    assert task.detect_events(None, {"structure_note": "bankruptcy"}) == []


def test_module_05_alerts_on_buying_frozen_transition():
    """buying_implication flipping to buying-frozen is a distinct alert path,
    even when structure_note doesn't change."""
    task = Module05StructuralNews()
    prev = {"structure_note": "merged with X", "buying_implication": "buying-friendly", "confidence": "high"}
    curr = {"structure_note": "merged with X", "buying_implication": "buying-frozen", "confidence": "high"}
    events = task.detect_events(prev, curr)
    assert any(e.signal_type == "structure-ambiguous" for e in events)


# ---- Module 07 ----

def test_module_06_alerts_on_new_funding_round_even_with_same_tag():
    """Two `funding round` tags in a row, different summaries — that's a second
    round and must fire a new alert. The naive tag-set diff would miss this."""
    task = Module06TriggerEvents()
    prev = {
        "triggers_detected": ["funding round"],
        "trigger_details": [
            {"trigger": "funding round", "summary": "Series A: $20M led by Acme",
             "url": "https://example.com/seriesA"},
        ],
    }
    curr = {
        "triggers_detected": ["funding round"],
        "trigger_details": [
            {"trigger": "funding round", "summary": "Series A: $20M led by Acme",
             "url": "https://example.com/seriesA"},
            {"trigger": "funding round", "summary": "Series B: $80M led by Bravo",
             "url": "https://example.com/seriesB"},
        ],
    }
    events = task.detect_events(prev, curr)
    assert len(events) == 1
    assert events[0].signal_type == "funding"
    assert "Series B" in events[0].summary


def test_module_06_alerts_on_agency_switch():
    task = Module06TriggerEvents()
    prev = {"trigger_details": []}
    curr = {
        "trigger_details": [
            {"trigger": "agency switch", "summary": "Switched from BBDO to Wieden+Kennedy",
             "url": "https://example.com/agency-news"},
        ],
    }
    events = task.detect_events(prev, curr)
    assert len(events) == 1
    assert events[0].signal_type == "agency-switch"


def test_module_06_no_alert_for_unmapped_trigger():
    """Triggers outside _TRIGGER_TO_SIGNAL (rebrand/campaign, AI initiative)
    write to Buying Signals but do NOT fire Needs Attention alerts."""
    task = Module06TriggerEvents()
    prev = {"trigger_details": []}
    curr = {
        "trigger_details": [
            {"trigger": "rebrand/campaign", "summary": "Refreshed visual identity"},
            {"trigger": "AI initiative", "summary": "Announced new AI ML lab"},
        ],
    }
    assert task.detect_events(prev, curr) == []


# ---- Module 14 ----

def test_module_11_alerts_on_tier1_role_closure():
    """Closed Tier 1 / Tier 2 important roles in curr_output fire an alert —
    company hired, team is in place, ramping creative ops."""
    task = Module11HiringSignal()
    prev = {"important_roles_open": [], "important_roles_recently_closed": []}
    curr = {
        "important_roles_open": [],
        "important_roles_recently_closed": [
            {"title": "Head of Creative AI", "tier": "tier_1_marketing_ai"},
            {"title": "Senior Brand Designer", "tier": "tier_3_senior_creative_ic"},  # Tier 3: no alert
        ],
    }
    events = task.detect_events(prev, curr)
    assert len(events) == 1
    assert events[0].signal_type == "senior-hire"
    assert "Head of Creative AI" in events[0].summary


def test_module_11_alerts_on_new_tier1_open_in_scope():
    task = Module11HiringSignal()
    prev = {
        "important_roles_open": [
            {"title": "Existing Role", "tier": "tier_1_marketing_ai", "in_scope": True},
        ],
        "important_roles_recently_closed": [],
    }
    curr = {
        "important_roles_open": [
            {"title": "Existing Role", "tier": "tier_1_marketing_ai", "in_scope": True},
            {"title": "Director of AI-Driven Marketing", "tier": "tier_1_marketing_ai",
             "in_scope": True, "location": "London, UK"},
        ],
        "important_roles_recently_closed": [],
    }
    events = task.detect_events(prev, curr)
    assert len(events) == 1
    assert events[0].signal_type == "senior-hire"
    assert "Director of AI-Driven Marketing" in events[0].summary
    assert "London" in events[0].summary


def test_module_11_no_alert_on_out_of_scope_tier1_open():
    """Out-of-scope Tier 1 opens (India, APAC) are too noisy for an alert."""
    task = Module11HiringSignal()
    prev = {"important_roles_open": [], "important_roles_recently_closed": []}
    curr = {
        "important_roles_open": [
            {"title": "Director of AI-Driven Marketing", "tier": "tier_1_marketing_ai",
             "in_scope": False, "location": "Bengaluru, India"},
        ],
        "important_roles_recently_closed": [],
    }
    assert task.detect_events(prev, curr) == []


# ---- Module 05 ----

def test_module_04_alerts_on_structure_type_change():
    task = Module04CorporateStructure()
    prev = {"structure_type": "standalone"}
    curr = {"structure_type": "subsidiary", "parent_company": "Acme Corp"}
    events = task.detect_events(prev, curr)
    assert len(events) == 1
    assert events[0].signal_type == "M&A"
    assert "standalone" in events[0].summary and "subsidiary" in events[0].summary


def test_module_04_no_alert_when_unchanged():
    task = Module04CorporateStructure()
    prev = {"structure_type": "standalone"}
    curr = {"structure_type": "standalone"}
    assert task.detect_events(prev, curr) == []


# ---- Module 03 (pain_points; was module_04 pre-rename) ----

def test_module_03_alerts_on_new_timing_pain_tag():
    task = Module03PainPoints()
    prev = {"tags": ["creative production", "strategy"]}
    curr = {"tags": ["creative production", "post-layoff overflow", "strategy"]}
    events = task.detect_events(prev, curr)
    assert len(events) == 1
    assert events[0].signal_type == "structure-ambiguous"
    assert "post-layoff overflow" in events[0].summary


def test_module_03_no_alert_when_narrative_tag_already_present():
    task = Module03PainPoints()
    prev = {"tags": ["post-layoff overflow", "creative production"]}
    curr = {"tags": ["post-layoff overflow", "strategy"]}
    assert task.detect_events(prev, curr) == []


def test_module_03_no_alert_for_non_timing_tags():
    """Most pain tags are narrative; only the timing-shift ones alert."""
    task = Module03PainPoints()
    prev = {"tags": []}
    curr = {"tags": ["creative production", "audience education", "competitive displacement"]}
    assert task.detect_events(prev, curr) == []


# ---- Orchestrator integration ----

def _iso(dt: datetime) -> str:
    return dt.isoformat()


class _ScriptedProvider:
    def __init__(self, response_text: str):
        self.name = "scripted"
        self.model = "scripted"
        self.response_text = response_text

    def run_loop(self, *args, **kwargs):
        return ProviderResult(
            text=self.response_text, tool_calls_made=0,
            input_tokens=10, output_tokens=10,
            iterations=1, stop_reason="end_turn", model_used=self.model,
        )


class _FakeCRM:
    def __init__(self):
        self.property_writes: list[Any] = []
        self.block_appends: list[Any] = []

    def update_properties(self, page_id, properties):
        self.property_writes.append((page_id, properties))

    def replace_latest_research_section(self, page_id, blocks, label=None):
        self.block_appends.append((page_id, list(blocks)))

    def find_latest_agent_section(self, page_id, label=None):
        return []

    def validate_schema(self, expected=None):
        return None


def _make_orch(rl):
    return Orchestrator(
        crm=_FakeCRM(), run_log=rl, provider=_ScriptedProvider("`json`"),
        concurrency=1,
    )


def _module_06_result(output: dict[str, Any], prompt_version: str = "v1.5.0"):
    """Build a synthetic TaskResult for module_06 with a given output."""
    from tasks.base import TaskResult
    return TaskResult(
        task_name="module_05_structural_news",
        output=output, confidence=output.get("confidence", "medium"),
        fields={}, page_blocks=[],
        section="News", subsection=None,
        sources=output.get("sources", []) or [],
        search_count=0,
        input_tokens=0, output_tokens=0,
        duration_seconds=0.0,
        prompt_version=prompt_version, provider_name="test",
    )


def test_orchestrator_collects_detected_events_in_outcome(tmp_path):
    """Pre-seed Module 06 with 'recent IPO'; synthetic current result is
    'bankruptcy'; _collect_detected_events surfaces the transition with the
    account_page_id stamped on each event."""
    rl = RunLog(tmp_path / "runs.db")
    orch = _make_orch(rl)
    account = Account(page_id="p1", name="Acme", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    freshness = {
        ("p1", "module_05_structural_news"): {
            "started_at": _iso(datetime.now(timezone.utc) - timedelta(days=10)),
            "output_json": json.dumps({"structure_note": "recent IPO",
                                        "event_date": "2025-12-01",
                                        "confidence": "high"}),
            "prompt_version": "v1.5.0",
            "confidence": "high",
        }
    }
    result = _module_06_result({
        "structure_note": "bankruptcy",
        "event_summary": "Chapter 7 filing",
        "event_date": "2026-05-10",
        "buying_implication": "buying-frozen",
        "confidence": "high",
        "sources": ["https://example.com/news"],
    })
    events = orch._collect_detected_events(
        account, [Module05StructuralNews()], [result], freshness,
    )
    signals = {e.signal_type for e in events}
    assert "bankruptcy" in signals
    assert all(e.account_page_id == "p1" for e in events)


def test_orchestrator_skips_diff_on_prompt_version_drift(tmp_path):
    """Prior row is v1.4.0, current run on v1.5.0 — diff is skipped."""
    rl = RunLog(tmp_path / "runs.db")
    orch = _make_orch(rl)
    account = Account(page_id="p1", name="Acme", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    freshness = {
        ("p1", "module_05_structural_news"): {
            "started_at": _iso(datetime.now(timezone.utc) - timedelta(days=10)),
            "output_json": json.dumps({"structure_note": "recent IPO", "confidence": "high"}),
            "prompt_version": "v1.4.0",  # older
            "confidence": "high",
        }
    }
    result = _module_06_result(
        {"structure_note": "bankruptcy", "confidence": "high", "sources": []},
        prompt_version="v1.5.0",
    )
    events = orch._collect_detected_events(
        account, [Module05StructuralNews()], [result], freshness,
    )
    assert events == []


def test_orchestrator_first_run_no_events(tmp_path):
    """No prior row → no events even on a clear bankruptcy state."""
    rl = RunLog(tmp_path / "runs.db")
    orch = _make_orch(rl)
    account = Account(page_id="p1", name="Acme", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    result = _module_06_result({"structure_note": "bankruptcy", "confidence": "high", "sources": []})
    events = orch._collect_detected_events(
        account, [Module05StructuralNews()], [result], {},
    )
    assert events == []
