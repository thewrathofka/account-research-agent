"""Module 6 — Structural news (last 6 months). Output → Structure Notes property + News page section."""

from __future__ import annotations

from datetime import date as _date
from typing import Any

import crm
import prompts.module_05_structural_news as prompt
from tasks.base import DetectedEvent, Task


# Mapping from the constrained structure_note vocabulary to the NEEDS_ATTENTION
# signal tag. structure_notes not in this map are non-alerting (positive but
# not load-bearing, e.g. "buying-friendly" alone — wait until severity 2+).
_STRUCTURE_NOTE_SEVERITY: dict[str, tuple[int, str]] = {
    # (severity, signal_type). Higher severity = stronger alert. Alert fires
    # when curr severity > prev severity (or prev is None).
    "recent IPO":      (2, "M&A"),
    "about to IPO":    (2, "M&A"),
    "merged with X":   (2, "M&A"),
    "acquired X":      (2, "M&A"),
    "acquired by X":   (2, "M&A"),
    "split from X":    (2, "M&A"),
    "mass layoffs":    (3, "layoffs"),
    "bankruptcy":      (4, "bankruptcy"),
    "out of business": (4, "bankruptcy"),
    "buying-frozen":   (3, "structure-ambiguous"),
}


def _severity(note: str | None) -> int:
    if not note:
        return 0
    return _STRUCTURE_NOTE_SEVERITY.get(note, (1, ""))[0]


class Module05StructuralNews(Task):
    name = "module_05_structural_news"
    section = "News"
    subsection = None
    prompt_module = prompt
    model_tier = "fast"  # news lookup; Haiku/mini handles fine
    synthesis_only = True   # reads ResearchPass output, no own tools

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        note = output.get("structure_note")
        if note:
            fields[crm.PROP_STRUCTURE_NOTES] = {
                "rich_text": [{"type": "text", "text": {"content": note}}]
            }
        return fields

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        note = output.get("structure_note")
        if not note:
            return [crm.paragraph("No significant structural events in the last 6 months.")]

        summary = output.get("event_summary") or note
        date_str = output.get("event_date")
        implication = output.get("buying_implication")

        text = f"{note.title()}: {summary}"
        if date_str:
            text += f" ({date_str})"
        if implication:
            text += f" — {implication}"
        return [crm.paragraph(text)]

    def detect_events(
        self,
        prev_output: dict[str, Any] | None,
        curr_output: dict[str, Any] | None,
    ) -> list[DetectedEvent]:
        if prev_output is None or curr_output is None:
            return []
        events: list[DetectedEvent] = []

        # 1. structure_note transition to higher severity.
        prev_note = prev_output.get("structure_note")
        curr_note = curr_output.get("structure_note")
        if curr_note and _severity(curr_note) > _severity(prev_note):
            signal_type = _STRUCTURE_NOTE_SEVERITY.get(curr_note, (0, ""))[1]
            if signal_type:
                summary = (
                    f"Structural state shifted to '{curr_note}'"
                    + (f": {curr_output.get('event_summary')}" if curr_output.get("event_summary") else "")
                )
                source_url = _first_source(curr_output)
                # Signature includes event_date when present so a *second* wave
                # (same note, later date) doesn't dedup against the first.
                event_date = curr_output.get("event_date") or ""
                signature = f"module_05:structure_note:{curr_note}:{event_date}"
                events.append(DetectedEvent(
                    account_page_id="",  # filled in by orchestrator
                    module=self.name, signal_type=signal_type,
                    summary=summary, source_url=source_url, signature=signature,
                ))

        # 2. buying_implication transition to buying-frozen — the buying window
        # is closing. Distinct alert from the structure_note path because the
        # implication can flip independently (e.g. M&A → buying-frozen for the
        # acquiring side, buying-friendly for the spun-off side).
        prev_impl = prev_output.get("buying_implication")
        curr_impl = curr_output.get("buying_implication")
        if curr_impl == "buying-frozen" and prev_impl != "buying-frozen":
            signature = f"module_05:buying_frozen:{curr_output.get('event_date') or ''}"
            events.append(DetectedEvent(
                account_page_id="",
                module=self.name, signal_type="structure-ambiguous",
                summary="Buying window closing: implication flipped to buying-frozen.",
                source_url=_first_source(curr_output), signature=signature,
            ))

        # 3. Same note, much later event_date — second wave of the same event
        # type (e.g. two rounds of layoffs at different dates).
        if (curr_note and prev_note == curr_note
                and curr_output.get("event_date") and prev_output.get("event_date")):
            if _months_apart(prev_output["event_date"], curr_output["event_date"]) >= 1:
                signal_type = _STRUCTURE_NOTE_SEVERITY.get(curr_note, (0, ""))[1]
                if signal_type:
                    signature = f"module_05:second_wave:{curr_note}:{curr_output['event_date']}"
                    events.append(DetectedEvent(
                        account_page_id="",
                        module=self.name, signal_type=signal_type,
                        summary=(
                            f"Second '{curr_note}' event ({curr_output['event_date']}); "
                            f"prior was {prev_output['event_date']}."
                        ),
                        source_url=_first_source(curr_output), signature=signature,
                    ))

        # 4. Confidence regression — high → low on the same module suggests the
        # world changed enough that the model can't classify it.
        if (prev_output.get("confidence") == "high"
                and curr_output.get("confidence") == "low"):
            today_iso = _date.today().isoformat()
            signature = f"module_05:confidence_regression:{today_iso}"
            events.append(DetectedEvent(
                account_page_id="",
                module=self.name, signal_type="structure-ambiguous",
                summary="Structural classification dropped from high to low confidence — manual review.",
                source_url=None, signature=signature,
            ))

        return events


def _first_source(output: dict[str, Any]) -> str | None:
    sources = output.get("sources") or []
    return sources[0] if sources else None


def _months_apart(iso_a: str, iso_b: str) -> int:
    """Rough month-distance between two ISO dates. Returns 0 on parse error."""
    try:
        a = _date.fromisoformat(iso_a[:10])
        b = _date.fromisoformat(iso_b[:10])
    except (ValueError, TypeError):
        return 0
    diff_days = abs((b - a).days)
    return diff_days // 30
