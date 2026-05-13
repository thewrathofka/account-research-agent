"""Module 7 — Trigger events (last 90 days).

Output:
- Buying Signals multi-select (the detected trigger tags — 2026-05-11 role swap)
- News page section: one bullet per trigger + a per-signal subsection appended
  under News by the orchestrator (heading + logic + sources)
"""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_07_trigger_events as prompt
from tasks.base import DetectedEvent, Task


# Trigger name → NEEDS_ATTENTION signal tag. Triggers absent from this map
# write to Buying Signals but do NOT fire a Needs Attention alert (rebrand/
# campaign and AI initiative are softer signals).
_TRIGGER_TO_SIGNAL: dict[str, str] = {
    "funding round": "funding",
    "agency switch": "agency-switch",
    "active creative jobs": "senior-hire",
}


def _trigger_signature(trigger: str, detail: dict[str, Any]) -> str:
    """Stable signature for diffing trigger_details. Uses URL when available
    (most stable across reruns), otherwise first 24 chars of summary."""
    url = (detail.get("url") or "").strip()
    if url:
        return f"module_07:{trigger}:url:{url}"
    summary = (detail.get("summary") or "").strip()[:24].lower()
    return f"module_07:{trigger}:summary:{summary}"


class Module07TriggerEvents(Task):
    name = "module_07_trigger_events"
    section = "News"
    subsection = None
    prompt_module = prompt
    synthesis_only = True   # reads ResearchPass output, no own tools
    model_tier = "fast"     # 2026-05-12 cost-cutting: trigger detection (funding /
                            # rebrand / agency switch / AI initiative) is keyword-
                            # adjacent extraction from research_pass, Haiku handles

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        triggers = output.get("triggers_detected") or []
        if not triggers:
            return {}
        # Filter against the canonical vocab so a stray label can't poison the
        # multi-select payload.
        valid = [t for t in triggers if t in crm.BUYING_SIGNAL_OPTIONS]
        if not valid:
            return {}
        return {
            crm.PROP_BUYING_SIGNALS: {
                "multi_select": [{"name": t} for t in valid],
            }
        }

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        details = output.get("trigger_details") or []
        if not details:
            return [crm.paragraph("No buying-signal triggers detected in the last 90 days.")]
        blocks: list[dict[str, Any]] = [crm.paragraph("Buying signals (last 90 days):")]
        for d in details:
            trigger = d.get("trigger", "?")
            summary = d.get("summary", "")
            blocks.append(crm.bullet(f"{trigger}: {summary}"))
        return blocks

    def to_signal_sections(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        details = output.get("trigger_details") or []
        sections: list[dict[str, Any]] = []
        for d in details:
            trigger = d.get("trigger")
            if trigger not in crm.BUYING_SIGNAL_OPTIONS:
                continue
            summary = d.get("summary") or ""
            per_url = d.get("url")
            sources = [per_url] if per_url else list(output.get("sources") or [])
            sections.append({"signal": trigger, "logic": summary, "sources": sources})
        return sections

    def detect_events(
        self,
        prev_output: dict[str, Any] | None,
        curr_output: dict[str, Any] | None,
    ) -> list[DetectedEvent]:
        """Diff per-trigger by URL/summary rather than by tag set — a second
        funding round shows the same `funding round` tag but a different
        summary, and that IS a new event."""
        if prev_output is None or curr_output is None:
            return []
        prev_details = prev_output.get("trigger_details") or []
        curr_details = curr_output.get("trigger_details") or []
        prev_sigs = {
            _trigger_signature(d.get("trigger") or "", d) for d in prev_details
        }
        events: list[DetectedEvent] = []
        for d in curr_details:
            trigger = d.get("trigger") or ""
            if trigger not in _TRIGGER_TO_SIGNAL:
                continue
            sig = _trigger_signature(trigger, d)
            if sig in prev_sigs:
                continue
            summary = (d.get("summary") or "").strip() or trigger
            events.append(DetectedEvent(
                account_page_id="",
                module=self.name,
                signal_type=_TRIGGER_TO_SIGNAL[trigger],
                summary=f"{trigger}: {summary[:240]}",
                source_url=d.get("url"),
                signature=sig,
            ))
        return events
