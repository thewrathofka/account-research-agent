"""Unit tests for the GATE behaviour: when module_01_gate determines a company
is out of scope, the orchestrator must skip ALL downstream tasks for that account.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crm import Account
from orchestrator import Orchestrator
from providers.base import ProviderResult
from run_log import RunLog


GATE_PASS_TEXT = """```json
{
  "company_name": "Acme",
  "size_band": "5000+",
  "operates_in_eu": true,
  "operates_in_na": true,
  "operates_in_eu_or_na": true,
  "sources": ["https://example.com"],
  "confidence": "high"
}
```"""


GATE_FAIL_TEXT = """```json
{
  "company_name": "Tiny Indian Fintech",
  "size_band": "<1000",
  "operates_in_eu": false,
  "operates_in_na": false,
  "operates_in_eu_or_na": false,
  "sources": ["https://example.com"],
  "confidence": "high",
  "reason_if_out_of_scope": "India-only fintech, no offices outside India per company website"
}
```"""


@dataclass
class ScriptedProvider:
    """Provider that returns a different canned response per task call."""
    name: str = "scripted"
    model: str = "scripted"
    responses: dict[str, str] = None  # task_name (via system_prompt fingerprint) -> text
    call_log: list[str] = None

    def __post_init__(self):
        self.responses = self.responses or {}
        self.call_log = []

    def run_loop(self, system_prompt, user_message, tools, max_iterations=10,
                 tool_call_cap=None, model_tier="smart", cache_system_prompt=True):
        # Fingerprint: which task is calling us, based on first-line of system_prompt
        first_line = system_prompt.splitlines()[0] if system_prompt else ""
        self.call_log.append(first_line[:80])
        text = self.responses.get(first_line[:80], '```json\n{"confidence":"high","sources":[]}\n```')
        return ProviderResult(
            text=text, tool_calls_made=0, input_tokens=10, output_tokens=10,
            iterations=1, stop_reason="end_turn", model_used=self.model,
        )


class FakeCRM:
    """Stand-in for NotionCRM that records writes instead of hitting the API."""
    def __init__(self):
        self.property_writes: list[tuple[str, dict[str, Any]]] = []
        self.block_appends: list[tuple[str, list[dict[str, Any]]]] = []
        self.archived_block_ids: list[str] = []

    def update_properties(self, page_id, properties):
        self.property_writes.append((page_id, properties))

    def append_blocks(self, page_id, blocks):
        self.block_appends.append((page_id, list(blocks)))

    def replace_latest_research_section(self, page_id, blocks, prefix=None):
        # Test stand-in for the idempotent write path. We don't model the
        # archive-old-blocks step here — the test doesn't preload prior content —
        # so this is equivalent to append_blocks for assertion purposes.
        self.append_blocks(page_id, blocks)

    def find_latest_agent_section(self, page_id, prefix=None):
        return []

    def validate_schema(self, expected=None):
        return None


def _make_orch(provider, dbpath):
    return Orchestrator(crm=FakeCRM(), run_log=RunLog(dbpath), provider=provider, concurrency=1)


def test_gate_pass_runs_downstream_tasks():
    """When the gate output indicates EU/NA presence, downstream tasks DO run."""
    with tempfile.TemporaryDirectory() as td:
        dbpath = Path(td) / "runs.db"
        provider = ScriptedProvider(responses={
            "You are a B2B sales research agent verifying ICP fit. Confirm the": GATE_PASS_TEXT,
            "You are a B2B sales research agent. Your job is to research a company": '```json\n{"company_name":"Acme","summary":"x","industry":"y","confidence":"high","sources":["http://a"]}\n```',
        })
        orch = _make_orch(provider, dbpath)
        account = Account(page_id="p1", name="Acme", rep="Katarina",
                          priority_type="Priority A", last_researched=None)
        outcomes = orch.run([account], ["module_01_gate", "company_overview"], dry_run=True)
        assert len(outcomes) == 1
        assert len(outcomes[0].task_results) == 2  # both ran
        assert outcomes[0].overall_status in ("done", "needs_review")


def test_gate_fail_skips_downstream_tasks():
    """When the gate output indicates no EU/NA presence, downstream tasks are SKIPPED."""
    with tempfile.TemporaryDirectory() as td:
        dbpath = Path(td) / "runs.db"
        provider = ScriptedProvider(responses={
            "You are a B2B sales research agent verifying ICP fit. Confirm the": GATE_FAIL_TEXT,
        })
        orch = _make_orch(provider, dbpath)
        account = Account(page_id="p1", name="Tiny Indian Fintech", rep="Katarina",
                          priority_type="Priority A", last_researched=None)
        outcomes = orch.run([account], ["module_01_gate", "company_overview"], dry_run=True)
        assert len(outcomes) == 1
        assert outcomes[0].overall_status == "out_of_scope"
        assert len(outcomes[0].task_results) == 1  # ONLY the gate ran
        assert outcomes[0].task_results[0].task_name == "module_01_gate"


def test_gate_fail_writes_out_of_scope_status_to_notion():
    """Live (non-dry-run) gate-fail path writes Research Status=out_of_scope and the reason."""
    with tempfile.TemporaryDirectory() as td:
        dbpath = Path(td) / "runs.db"
        provider = ScriptedProvider(responses={
            "You are a B2B sales research agent verifying ICP fit. Confirm the": GATE_FAIL_TEXT,
        })
        orch = _make_orch(provider, dbpath)
        account = Account(page_id="p1", name="Tiny Indian Fintech", rep="Katarina",
                          priority_type="Priority A", last_researched=None)
        outcomes = orch.run([account], ["module_01_gate", "company_overview"], dry_run=False)
        assert outcomes[0].overall_status == "out_of_scope"
        assert outcomes[0].wrote_to_notion is True

        # Verify the FakeCRM saw the right writes.
        prop_writes = orch.crm.property_writes
        assert len(prop_writes) == 1
        page_id, props = prop_writes[0]
        assert page_id == "p1"
        assert props["Research Status"]["select"]["name"] == "out_of_scope"

        # Block appends should include the reason text.
        assert len(orch.crm.block_appends) == 1
        _, blocks = orch.crm.block_appends[0]
        block_texts = []
        for b in blocks:
            if "paragraph" in b:
                block_texts.extend(rt["text"]["content"] for rt in b["paragraph"]["rich_text"])
        joined = " ".join(block_texts)
        assert "India-only" in joined or "out of scope" in joined.lower()
