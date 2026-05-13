"""Shared Notion writeback (Fix Appendix #15, #16, #2, #3).

Why this module exists
----------------------
Before the fix appendix, both `orchestrator.py` (sync path) and
`batch_runner.py` (batch path) duplicated the writeback logic — merging
per-task `fields` dicts into a single Notion property payload, building the
page-body section, and writing them in the wrong order. The drift between the
two paths is exactly how out-of-scope accounts ended up with `wrote_to_notion=False`
in batch mode while the sync path wrote them correctly. This module is the
single source of truth for "given a list of TaskResults, persist the outcome
to Notion correctly and idempotently".

Three behaviour fixes baked into the contract:
- #16 explicit merge strategies — multi-selects union, status fields take max
  severity, rich_text appends, default is replace.
- #2 idempotent block appends — uses CRM.replace_latest_research_section so a
  rerun never duplicates research sections on the same page.
- #3 ordering — write generated content (pre-body properties + page blocks)
  FIRST, then the completion properties (Last Researched, Status, Confidence).
  This way a write failure on the body never leaves the row marked researched.

The dry_run path returns False without making any Notion calls; callers should
build their `wrote_to_notion` flag from the boolean return.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable

import crm as crm_module
from crm import Account, NotionCRM
from tasks.base import TaskResult


log = logging.getLogger(__name__)


# ---- Merge strategies (Fix Appendix #16) ----

# A merge function takes (existing_value_or_None, incoming_value, prop_name)
# and returns the merged value.
MergeFn = Callable[[dict[str, Any] | None, dict[str, Any], str], dict[str, Any]]


def merge_multi_select(
    existing: dict[str, Any] | None, incoming: dict[str, Any], _prop: str,
) -> dict[str, Any]:
    """Union options by name; preserve insertion order so the first appearance wins."""
    if existing is None:
        return incoming
    seen = {item["name"] for item in existing.get("multi_select", [])}
    merged = list(existing.get("multi_select", []))
    for item in incoming.get("multi_select", []):
        if item["name"] not in seen:
            merged.append(item)
            seen.add(item["name"])
    return {"multi_select": merged}


# Severity order for status-like select fields. Higher = worse → wins.
_STATUS_SEVERITY = {
    "pending": 0,
    "done": 1,
    "out_of_scope": 2,
    "needs_review": 3,
    "failed": 4,
}


def merge_status_priority(
    existing: dict[str, Any] | None, incoming: dict[str, Any], _prop: str,
) -> dict[str, Any]:
    """Take the more-severe status, not whichever task happened to write last."""
    if existing is None:
        return incoming

    def _rank(name: str | None) -> int:
        return _STATUS_SEVERITY.get(name or "", -1)

    a = existing.get("select", {}).get("name")
    b = incoming.get("select", {}).get("name")
    return existing if _rank(a) >= _rank(b) else incoming


def merge_rich_text_append(
    existing: dict[str, Any] | None, incoming: dict[str, Any], _prop: str,
) -> dict[str, Any]:
    """Concatenate rich_text arrays. Adds a separator paragraph between sources."""
    if existing is None:
        return incoming
    sep = [{"type": "text", "text": {"content": " · "}}]
    return {
        "rich_text": (
            existing.get("rich_text", []) + sep + incoming.get("rich_text", [])
        )
    }


def replace_value(
    _existing: dict[str, Any] | None, incoming: dict[str, Any], _prop: str,
) -> dict[str, Any]:
    """Default: incoming wins. Use only for fields with one canonical source."""
    return incoming


MERGE_STRATEGIES: dict[str, MergeFn] = {
    crm_module.PROP_BUYING_SIGNALS: merge_multi_select,
    crm_module.PROP_BUYING_INTENT: merge_multi_select,
    crm_module.PROP_PAIN_POINT_TAGS: merge_multi_select,
    crm_module.PROP_STRUCTURE_NOTES: merge_rich_text_append,
    crm_module.PROP_RESEARCH_STATUS: merge_status_priority,
}


def merge_property(
    existing: dict[str, Any] | None, incoming: dict[str, Any], prop_name: str,
) -> dict[str, Any]:
    return MERGE_STRATEGIES.get(prop_name, replace_value)(existing, incoming, prop_name)


# ---- Property + block payload builders ----

def build_property_payload(
    results: list[TaskResult],
) -> dict[str, Any]:
    """Merge per-task `fields` dicts into one Notion property payload.

    Multi-selects union (within a run), status-like fields take max severity,
    rich_text appends, and any other property is replaced by the incoming value.

    PROP_BUYING_SIGNALS is always present in the payload (default empty list)
    because the agent is the sole owner of that property post-2026-05-11. If a
    rerun finds zero signals, Notion's partial-update semantics would otherwise
    leave stale tags from a previous run; writing an empty multi_select clears
    them. PROP_BUYING_INTENT is intentionally NOT defaulted — that property is
    human-managed and the agent must never touch it.
    """
    payload: dict[str, Any] = {}
    for r in results:
        for prop_name, value in r.fields.items():
            payload[prop_name] = merge_property(payload.get(prop_name), value, prop_name)
    payload.setdefault(crm_module.PROP_BUYING_SIGNALS, {"multi_select": []})
    return payload


def build_completion_payload(
    overall_confidence: str, overall_status: str,
) -> dict[str, Any]:
    """Properties written ONLY after the page body succeeds (Fix Appendix #3)."""
    return {
        crm_module.PROP_LAST_RESEARCHED: {"date": {"start": date.today().isoformat()}},
        crm_module.PROP_RESEARCH_CONFIDENCE: {
            "select": {"name": overall_confidence if overall_confidence != "failed" else "low"}
        },
        crm_module.PROP_RESEARCH_STATUS: {"select": {"name": overall_status}},
    }


# ---- The unified writeback entry point ----

def write_account_outcome(
    crm: NotionCRM,
    account: Account,
    results: list[TaskResult],
    overall_status: str,
    overall_confidence: str,
    research_blocks: list[dict[str, Any]] | None,
    dry_run: bool = False,
    label: str | None = None,
) -> bool:
    """Atomic-ish writeback for one account.

    Order (Fix Appendix #3):
      1. Pre-body properties (Size, Buying Signals, structure notes, etc.)
      2. Page body via CRM.replace_latest_research_section (Fix #2 — idempotent)
      3. Completion properties (Last Researched / Status / Confidence)

    `label` selects which prior section to replace — labeled runs only
    overwrite the prior section with the same label, so multiple variants
    coexist on the same page for side-by-side comparison.

    Returns True iff Notion writes happened. dry_run=True returns False without
    side effects. On error, the exception bubbles to the caller — they decide
    how to surface it (orchestrator wraps it in AccountOutcome.error).
    """
    if dry_run:
        return False

    pre_body_props = build_property_payload(results)
    if pre_body_props:
        crm.update_properties(account.page_id, pre_body_props)

    if research_blocks:
        crm.replace_latest_research_section(
            account.page_id, research_blocks, label=label,
        )

    completion_props = build_completion_payload(overall_confidence, overall_status)
    crm.update_properties(account.page_id, completion_props)
    return True


def write_gate_failure_to_notion(
    crm: NotionCRM,
    account: Account,
    results: list[TaskResult],
    confidence: str,
    dry_run: bool = False,
    label: str | None = None,
) -> bool:
    """Write the out-of-scope marker plus a small reason block.

    Used by both sync and batch paths so gated accounts have identical Notion
    state regardless of mode (Fix Appendix #4). `label` is propagated to the
    section heading so labeled gate-failure runs coexist with labeled passes
    on the same page.
    """
    if dry_run:
        return False

    from tasks import GATE_TASKS
    gate_result = next((r for r in results if r.task_name in GATE_TASKS), None)

    heading_text = crm_module.build_section_heading_text(
        date.today().isoformat(), label,
    )
    blocks: list[dict[str, Any]] = [
        crm_module.heading_2(heading_text),
        crm_module.paragraph("Out of scope: gate failed (no EU/NA operations confirmed)."),
    ]
    if gate_result and gate_result.output:
        reason = gate_result.output.get("reason_if_out_of_scope")
        if reason:
            blocks.append(crm_module.paragraph(f"Reason: {reason}"))
    blocks.append(crm_module.divider())

    crm.replace_latest_research_section(account.page_id, blocks, label=label)
    crm.update_properties(
        account.page_id,
        build_completion_payload(confidence, "out_of_scope"),
    )
    return True
