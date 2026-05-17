"""Tests for Phase A — per-module freshness gating.

Covers:
- RunLog.latest_successful_runs filters out failed/dry_run/confidence=failed rows.
- Orchestrator skips a task whose latest successful row is within the threshold.
- Orchestrator injects the prior output into the context envelope when skipping
  (so downstream tasks that read the upstream module still see its data).
- Orchestrator runs the task when stale OR when no prior row exists.
- Module 11 (hiring_signal, which reads module_05_structural_news context) still
  gets its context when module_05 is skipped on freshness.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from crm import Account
from orchestrator import Orchestrator
from providers.base import ProviderResult
from run_log import RunLog, RunRecord
from account_research_agent import _parse_module_since


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---- RunLog.latest_successful_runs ----

def test_latest_successful_runs_returns_most_recent_per_pair(tmp_path) -> None:
    rl = RunLog(tmp_path / "runs.db")
    now = datetime.now(timezone.utc)
    rl.record(RunRecord(
        account_page_id="p1", account_name="A", task_name="module_05_structural_news",
        started_at=_iso(now - timedelta(days=3)), completed_at=_iso(now - timedelta(days=3)),
        status="success", confidence="medium",
        output_json='{"structure_note":null,"confidence":"medium"}',
    ))
    rl.record(RunRecord(
        account_page_id="p1", account_name="A", task_name="module_05_structural_news",
        started_at=_iso(now - timedelta(hours=1)), completed_at=_iso(now - timedelta(hours=1)),
        status="success", confidence="high",
        output_json='{"structure_note":"recent IPO","confidence":"high"}',
    ))
    result = rl.latest_successful_runs(["p1"])
    assert ("p1", "module_05_structural_news") in result
    row = result[("p1", "module_05_structural_news")]
    assert row["confidence"] == "high"
    assert '"recent IPO"' in row["output_json"]


def test_latest_successful_runs_excludes_failed_status(tmp_path) -> None:
    rl = RunLog(tmp_path / "runs.db")
    now = datetime.now(timezone.utc)
    rl.record(RunRecord(
        account_page_id="p1", account_name="A", task_name="module_05_structural_news",
        started_at=_iso(now - timedelta(days=3)), completed_at=_iso(now - timedelta(days=3)),
        status="success", confidence="medium",
        output_json='{"x":1}',
    ))
    # Newer failed row — should not be returned.
    rl.record(RunRecord(
        account_page_id="p1", account_name="A", task_name="module_05_structural_news",
        started_at=_iso(now - timedelta(hours=1)), completed_at=_iso(now - timedelta(hours=1)),
        status="failed", confidence="failed",
        error="boom",
    ))
    result = rl.latest_successful_runs(["p1"])
    row = result[("p1", "module_05_structural_news")]
    # MAX(started_at) returns the failed row's timestamp because the GROUP BY
    # uses the filtered row set — we want the successful one's timestamp.
    assert '"x":1' in row["output_json"]


def test_latest_successful_runs_excludes_confidence_failed(tmp_path) -> None:
    """The subtle case: status='success' but confidence='failed' (model
    self-reported no usable signal). Must NOT count as fresh."""
    rl = RunLog(tmp_path / "runs.db")
    now = datetime.now(timezone.utc)
    rl.record(RunRecord(
        account_page_id="p1", account_name="A", task_name="module_05_structural_news",
        started_at=_iso(now - timedelta(days=3)), completed_at=_iso(now - timedelta(days=3)),
        status="success", confidence="medium", output_json='{"x":1}',
    ))
    # Newer success/confidence=failed — exclude.
    rl.record(RunRecord(
        account_page_id="p1", account_name="A", task_name="module_05_structural_news",
        started_at=_iso(now - timedelta(hours=1)), completed_at=_iso(now - timedelta(hours=1)),
        status="success", confidence="failed", output_json="null",
    ))
    result = rl.latest_successful_runs(["p1"])
    row = result[("p1", "module_05_structural_news")]
    assert '"x":1' in row["output_json"]


def test_latest_successful_runs_excludes_dry_run_rows(tmp_path) -> None:
    rl = RunLog(tmp_path / "runs.db")
    now = datetime.now(timezone.utc)
    rl.record(RunRecord(
        account_page_id="p1", account_name="A", task_name="module_05_structural_news",
        started_at=_iso(now - timedelta(hours=1)), completed_at=_iso(now - timedelta(hours=1)),
        status="success", confidence="high", dry_run=True, output_json='{"dry":true}',
    ))
    result = rl.latest_successful_runs(["p1"])
    assert ("p1", "module_05_structural_news") not in result


def test_latest_successful_runs_empty_input_returns_empty(tmp_path) -> None:
    rl = RunLog(tmp_path / "runs.db")
    assert rl.latest_successful_runs([]) == {}


# ---- _parse_module_since CLI parser ----

def test_parse_module_since_handles_empty() -> None:
    assert _parse_module_since(None) == {}
    assert _parse_module_since("") == {}


def test_parse_module_since_parses_pairs() -> None:
    spec = "module_05_structural_news:1,module_06_trigger_events:1,module_11_hiring_signal:7"
    out = _parse_module_since(spec)
    assert out == {
        "module_05_structural_news": 1,
        "module_06_trigger_events": 1,
        "module_11_hiring_signal": 7,
    }


def test_parse_module_since_rejects_unknown_task() -> None:
    import argparse
    with pytest.raises(argparse.ArgumentTypeError, match="unknown task"):
        _parse_module_since("module_99_fake:1")


def test_parse_module_since_rejects_non_integer_days() -> None:
    import argparse
    with pytest.raises(argparse.ArgumentTypeError, match="integer"):
        _parse_module_since("module_05_structural_news:soon")


def test_parse_module_since_rejects_non_positive_days() -> None:
    import argparse
    with pytest.raises(argparse.ArgumentTypeError, match="positive"):
        _parse_module_since("module_05_structural_news:0")


def test_parse_module_since_rejects_malformed_pair() -> None:
    import argparse
    with pytest.raises(argparse.ArgumentTypeError, match="task_name:DAYS"):
        _parse_module_since("module_05_structural_news")


# ---- Orchestrator freshness skip + context injection ----

@dataclass
class _CallCounter:
    """Provider stand-in that counts run_loop calls."""
    name: str = "counter"
    model: str = "counter"
    calls: int = 0

    def run_loop(self, system_prompt, user_message, tools, max_iterations=10,
                 tool_call_cap=None, model_tier="smart", cache_system_prompt=True):
        self.calls += 1
        return ProviderResult(
            text='```json\n{"confidence":"high","sources":[]}\n```',
            tool_calls_made=0, input_tokens=10, output_tokens=10,
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


def test_orchestrator_skips_task_when_fresh(tmp_path) -> None:
    """Pre-seed a recent successful row for a task; orchestrator should not
    call the LLM for that task when --module-since says it's fresh."""
    rl = RunLog(tmp_path / "runs.db")
    now = datetime.now(timezone.utc)
    rl.record(RunRecord(
        account_page_id="p1", account_name="Acme", task_name="company_overview",
        started_at=_iso(now - timedelta(hours=2)), completed_at=_iso(now - timedelta(hours=2)),
        status="success", confidence="medium",
        output_json='{"company_name":"Acme","summary":"x","industry":"y","confidence":"medium","sources":["https://a"]}',
    ))
    provider = _CallCounter()
    orch = Orchestrator(
        crm=_FakeCRM(), run_log=rl, provider=provider, concurrency=1,
        module_since={"company_overview": 1},  # 1-day threshold; row is 2h old
    )
    account = Account(page_id="p1", name="Acme", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    outcomes = orch.run([account], ["company_overview"], dry_run=True)
    assert provider.calls == 0, "fresh row should have prevented the LLM call"
    assert len(outcomes[0].task_results) == 1
    assert outcomes[0].task_results[0].confidence == "medium"


def test_orchestrator_runs_task_when_stale(tmp_path) -> None:
    """Pre-seed an old successful row; --module-since threshold has lapsed
    so the task must run."""
    rl = RunLog(tmp_path / "runs.db")
    now = datetime.now(timezone.utc)
    rl.record(RunRecord(
        account_page_id="p1", account_name="Acme", task_name="company_overview",
        started_at=_iso(now - timedelta(days=5)), completed_at=_iso(now - timedelta(days=5)),
        status="success", confidence="medium",
        output_json='{"x":1}',
    ))
    provider = _CallCounter()
    orch = Orchestrator(
        crm=_FakeCRM(), run_log=rl, provider=provider, concurrency=1,
        module_since={"company_overview": 1},  # 1-day threshold; row is 5d old
    )
    account = Account(page_id="p1", name="Acme", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    orch.run([account], ["company_overview"], dry_run=True)
    assert provider.calls == 1, "stale row should not have prevented the LLM call"


def test_orchestrator_runs_task_when_no_prior_row(tmp_path) -> None:
    """No prior row at all — must run, even though module_since says X days fresh."""
    rl = RunLog(tmp_path / "runs.db")
    provider = _CallCounter()
    orch = Orchestrator(
        crm=_FakeCRM(), run_log=rl, provider=provider, concurrency=1,
        module_since={"company_overview": 30},
    )
    account = Account(page_id="p1", name="Acme", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    orch.run([account], ["company_overview"], dry_run=True)
    assert provider.calls == 1


def test_skipped_task_injects_prior_output_into_context(tmp_path) -> None:
    """When a task is skipped on freshness, its prior output_json must be
    available in context[task.name] for any downstream tasks that read it."""
    rl = RunLog(tmp_path / "runs.db")
    now = datetime.now(timezone.utc)
    rl.record(RunRecord(
        account_page_id="p1", account_name="Acme", task_name="company_overview",
        started_at=_iso(now - timedelta(hours=1)), completed_at=_iso(now - timedelta(hours=1)),
        status="success", confidence="medium",
        output_json='{"company_name":"Acme","summary":"prior summary stays","industry":"saas","confidence":"medium","sources":["https://prior"]}',
    ))

    captured: dict[str, Any] = {}

    class _CaptureProvider(_CallCounter):
        def run_loop(self, system_prompt, user_message, tools, max_iterations=10,
                     tool_call_cap=None, model_tier="smart", cache_system_prompt=True):
            # Record what user_message looks like so we can assert prior
            # output flows into a downstream task's context.
            captured.setdefault("messages", []).append(user_message)
            return super().run_loop(system_prompt, user_message, tools, max_iterations,
                                    tool_call_cap, model_tier, cache_system_prompt)

    provider = _CaptureProvider()
    orch = Orchestrator(
        crm=_FakeCRM(), run_log=rl, provider=provider, concurrency=1,
        module_since={"company_overview": 1},
    )
    account = Account(page_id="p1", name="Acme", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    outcomes = orch.run([account], ["company_overview"], dry_run=True)

    # company_overview was skipped — no LLM call. Its synthesized TaskResult
    # must carry the prior output for downstream writeback / context use.
    assert provider.calls == 0
    synth = outcomes[0].task_results[0]
    assert synth.task_name == "company_overview"
    assert synth.output is not None
    assert synth.output.get("summary") == "prior summary stays"


def test_completion_payload_always_updates_last_researched() -> None:
    """Every agent touch — daily, weekly, monthly, quarterly — bumps the
    `Last Researched` date. The property's semantics: "when did the agent
    last update anything on this page." Used by Kali for freshness-at-a-
    glance in the CRM; partial refreshes should still count."""
    from writeback import build_completion_payload
    import crm as crm_module

    payload = build_completion_payload("high", "done")
    assert crm_module.PROP_LAST_RESEARCHED in payload
    assert crm_module.PROP_RESEARCH_CONFIDENCE in payload
    assert crm_module.PROP_RESEARCH_STATUS in payload


def test_skipped_task_with_no_output_json_falls_through(tmp_path) -> None:
    """A fresh row whose output_json is null/empty can't replay context, so
    the orchestrator must fall through to a real LLM run instead of writing
    a hollow synthesized result."""
    rl = RunLog(tmp_path / "runs.db")
    now = datetime.now(timezone.utc)
    rl.record(RunRecord(
        account_page_id="p1", account_name="Acme", task_name="company_overview",
        started_at=_iso(now - timedelta(hours=1)), completed_at=_iso(now - timedelta(hours=1)),
        status="success", confidence="medium",
        output_json=None,
    ))
    provider = _CallCounter()
    orch = Orchestrator(
        crm=_FakeCRM(), run_log=rl, provider=provider, concurrency=1,
        module_since={"company_overview": 1},
    )
    account = Account(page_id="p1", name="Acme", rep="Katarina",
                      priority_type="Priority A", last_researched=None)
    orch.run([account], ["company_overview"], dry_run=True)
    assert provider.calls == 1, "missing output_json should force a real run"
