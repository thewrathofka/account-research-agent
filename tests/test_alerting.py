"""Tests for Phase C — Notion alerting (Needs Attention + comment-per-run).

Covers:
- event_alerts SQLite ledger (record_alert, recent_alert) — round-trip + cooldown.
- Orchestrator._filter_and_record_events dedup contract:
  * Drop events whose signature was alerted within the cooldown window AND
    the human hasn't set Attention Acknowledged At since.
  * Re-fire when ack date is newer than the last alert.
  * Always fire when no prior alert exists.
- Writeback Needs Attention property + comment-per-run:
  * Tag set is unioned (merge_multi_select), never cleared by agent.
  * Exactly ONE crm.create_comment call per non-empty event list.
  * Comment body lists every event.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

import crm as crm_module
from crm import Account
from orchestrator import Orchestrator
from providers.base import ProviderResult
from run_log import RunLog
from tasks.base import DetectedEvent, TaskResult
from writeback import _build_alert_comment, write_account_outcome


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---- event_alerts ledger ----

def test_recent_alert_returns_none_when_no_prior(tmp_path):
    rl = RunLog(tmp_path / "runs.db")
    assert rl.recent_alert("p1", "sig:bankruptcy:2026-05-10") is None


def test_record_then_recent_alert_returns_timestamp(tmp_path):
    rl = RunLog(tmp_path / "runs.db")
    rl.record_alert("p1", "sig:bankruptcy:2026-05-10", module="module_06",
                    signal_type="bankruptcy", summary="x")
    last = rl.recent_alert("p1", "sig:bankruptcy:2026-05-10")
    assert last is not None


def test_recent_alert_respects_cooldown_window(tmp_path):
    """An alert older than the window should not show up under within_days."""
    import sqlite3
    rl = RunLog(tmp_path / "runs.db")
    rl.record_alert("p1", "sig:old", signal_type="layoffs", summary="x")
    # Manually backdate the row beyond the 14-day window.
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with sqlite3.connect(tmp_path / "runs.db") as c:
        c.execute("UPDATE event_alerts SET alerted_at = ? WHERE signature = ?",
                  (old_ts, "sig:old"))
        c.commit()
    assert rl.recent_alert("p1", "sig:old", within_days=14) is None


# ---- Orchestrator dedup ----

class _NoopProvider:
    name = "noop"
    model = "noop"

    def run_loop(self, *args, **kwargs):
        return ProviderResult(text="", tool_calls_made=0, input_tokens=0,
                              output_tokens=0, iterations=1, stop_reason="end_turn",
                              model_used=self.model)


class _FakeCRMWithAck:
    """CRM stand-in that records writes and returns a configurable
    Attention Acknowledged At date so dedup tests can exercise the
    acknowledgement-clears-cooldown contract."""
    def __init__(self, acknowledged_at: str | None = None):
        self.acknowledged_at = acknowledged_at
        self.property_writes: list[Any] = []
        self.block_appends: list[Any] = []
        self.comments: list[tuple[str, str]] = []

    def update_properties(self, page_id, properties):
        self.property_writes.append((page_id, properties))

    def replace_latest_research_section(self, page_id, blocks, label=None):
        self.block_appends.append((page_id, list(blocks)))

    def find_latest_agent_section(self, page_id, label=None):
        return []

    def validate_schema(self, expected=None):
        return None

    def create_comment(self, page_id, body):
        self.comments.append((page_id, body))

    def get_attention_acknowledged_at(self, page_id):
        return self.acknowledged_at


def _make_orch(rl, crm):
    return Orchestrator(crm=crm, run_log=rl, provider=_NoopProvider(), concurrency=1)


def _ev(signature: str = "sig:x", signal_type: str = "bankruptcy") -> DetectedEvent:
    return DetectedEvent(
        account_page_id="p1", module="module_06_structural_news",
        signal_type=signal_type, summary="An event happened.",
        signature=signature, source_url="https://example.com/news",
    )


def test_filter_fires_when_no_prior_alert(tmp_path):
    rl = RunLog(tmp_path / "runs.db")
    crm = _FakeCRMWithAck(acknowledged_at=None)
    orch = _make_orch(rl, crm)
    account = Account(page_id="p1", name="A", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    fired = orch._filter_and_record_events(account, [_ev("sig:1")])
    assert len(fired) == 1
    # Ledger row recorded.
    assert rl.recent_alert("p1", "sig:1") is not None


def test_filter_dedups_when_recently_alerted_and_unacknowledged(tmp_path):
    rl = RunLog(tmp_path / "runs.db")
    rl.record_alert("p1", "sig:1", signal_type="bankruptcy", summary="x")
    crm = _FakeCRMWithAck(acknowledged_at=None)
    orch = _make_orch(rl, crm)
    account = Account(page_id="p1", name="A", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    fired = orch._filter_and_record_events(account, [_ev("sig:1")])
    assert fired == []  # deduped


def test_filter_refires_when_ack_is_newer_than_last_alert(tmp_path):
    """The human acknowledged AFTER the last alert → next run with the same
    signature must fire again."""
    import sqlite3
    rl = RunLog(tmp_path / "runs.db")
    rl.record_alert("p1", "sig:1", signal_type="bankruptcy", summary="x")
    # Backdate the alert to yesterday.
    old_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with sqlite3.connect(tmp_path / "runs.db") as c:
        c.execute("UPDATE event_alerts SET alerted_at = ? WHERE signature = ?",
                  (old_ts, "sig:1"))
        c.commit()
    # Ack as of today (newer than the backdated alert).
    crm = _FakeCRMWithAck(acknowledged_at=date.today().isoformat())
    orch = _make_orch(rl, crm)
    account = Account(page_id="p1", name="A", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    fired = orch._filter_and_record_events(account, [_ev("sig:1")])
    assert len(fired) == 1


def test_filter_handles_get_ack_failure_gracefully(tmp_path):
    """A Notion read failure shouldn't crash dedup; treat as "no ack" and
    apply the cooldown normally."""
    rl = RunLog(tmp_path / "runs.db")
    rl.record_alert("p1", "sig:1", signal_type="bankruptcy", summary="x")

    class _BrokenCRM(_FakeCRMWithAck):
        def get_attention_acknowledged_at(self, page_id):
            raise RuntimeError("Notion unavailable")

    crm = _BrokenCRM()
    orch = _make_orch(rl, crm)
    account = Account(page_id="p1", name="A", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    fired = orch._filter_and_record_events(account, [_ev("sig:1")])
    # Recent alert + no ack reachable → suppressed.
    assert fired == []


# ---- Writeback alerting integration ----

def _result(task_name="module_06_structural_news") -> TaskResult:
    """Minimal valid TaskResult for writeback testing."""
    return TaskResult(
        task_name=task_name, output={}, confidence="high",
        fields={}, page_blocks=[],
        section="News", subsection=None,
        sources=[], search_count=0,
        input_tokens=0, output_tokens=0, duration_seconds=0.0,
        prompt_version="v1.5.0", provider_name="test",
    )


def test_writeback_sets_needs_attention_tags_from_events(tmp_path):
    crm = _FakeCRMWithAck()
    account = Account(page_id="p1", name="Acme", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    events = [
        DetectedEvent(account_page_id="p1", module="module_06_structural_news",
                      signal_type="bankruptcy", summary="...", signature="s1"),
        DetectedEvent(account_page_id="p1", module="module_07_trigger_events",
                      signal_type="funding", summary="...", signature="s2"),
    ]
    write_account_outcome(
        crm, account, [_result()],
        overall_status="done", overall_confidence="high",
        research_blocks=None, label=None, detected_events=events,
    )
    # Pre-body update should carry Needs Attention multi-select.
    pre_props = crm.property_writes[0][1]
    tags = [item["name"] for item in pre_props[crm_module.PROP_NEEDS_ATTENTION]["multi_select"]]
    assert "bankruptcy" in tags
    assert "funding" in tags


def test_writeback_posts_exactly_one_comment_per_event_list(tmp_path):
    crm = _FakeCRMWithAck()
    account = Account(page_id="p1", name="Acme", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    events = [
        DetectedEvent(account_page_id="p1", module="module_06_structural_news",
                      signal_type="bankruptcy", summary="Chapter 7 filed", signature="s1"),
        DetectedEvent(account_page_id="p1", module="module_06_structural_news",
                      signal_type="structure-ambiguous", summary="Buying window closing",
                      signature="s2"),
    ]
    write_account_outcome(
        crm, account, [_result()],
        overall_status="done", overall_confidence="high",
        research_blocks=None, label=None, detected_events=events,
    )
    assert len(crm.comments) == 1
    body = crm.comments[0][1]
    assert "Chapter 7" in body
    assert "Buying window closing" in body
    assert "bankruptcy" in body
    assert "structure-ambiguous" in body


def test_writeback_no_comment_when_no_events(tmp_path):
    crm = _FakeCRMWithAck()
    account = Account(page_id="p1", name="Acme", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    write_account_outcome(
        crm, account, [_result()],
        overall_status="done", overall_confidence="high",
        research_blocks=None, label=None, detected_events=[],
    )
    assert crm.comments == []
    # Needs Attention not set when there are no events to surface.
    pre_props = crm.property_writes[0][1] if crm.property_writes else {}
    assert crm_module.PROP_NEEDS_ATTENTION not in pre_props


def test_writeback_drops_unknown_signal_types():
    """A DetectedEvent with a signal_type outside NEEDS_ATTENTION_OPTIONS
    should not poison the multi-select payload."""
    crm = _FakeCRMWithAck()
    account = Account(page_id="p1", name="Acme", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    events = [
        DetectedEvent(account_page_id="p1", module="module_06_structural_news",
                      signal_type="some-future-signal", summary="x", signature="s1"),
    ]
    write_account_outcome(
        crm, account, [_result()],
        overall_status="done", overall_confidence="high",
        research_blocks=None, label=None, detected_events=events,
    )
    # Comment still fires (we want the human to see the future signal in
    # the audit trail), but no Needs Attention tag is set.
    pre_props = crm.property_writes[0][1] if crm.property_writes else {}
    assert crm_module.PROP_NEEDS_ATTENTION not in pre_props
    assert len(crm.comments) == 1


def test_build_alert_comment_includes_ack_instruction():
    events = [
        DetectedEvent(account_page_id="p1", module="m", signal_type="bankruptcy",
                      summary="An event", signature="s",
                      source_url="https://example.com"),
    ]
    body = _build_alert_comment(events)
    assert "An event" in body
    assert "https://example.com" in body
    assert "Attention Acknowledged At" in body
    assert "bankruptcy" in body