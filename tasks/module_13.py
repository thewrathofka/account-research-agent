"""Module 13 — Industry pulse (last 60 days).

When industry_movement_detected=true, appends `industry movement` to the agent-only
Buying Signals multi-select (post-2026-05-11 role swap) and emits a per-signal
subsection under News (heading + logic + sources). The story bullets also land
in the Competitor Landscape section as a paragraph.
"""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_13_industry_pulse as prompt
from tasks.base import Task


class Module13IndustryPulse(Task):
    name = "module_13_industry_pulse"
    section = "Competitor Landscape"
    subsection = None
    prompt_module = prompt
    model_tier = "fast"  # news headlines lookup; Haiku/mini handles fine
    synthesis_only = True   # reads ResearchPass output, no own tools

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        if output.get("industry_movement_detected"):
            return {
                crm.PROP_BUYING_SIGNALS: {
                    "multi_select": [{"name": "industry movement"}],
                }
            }
        return {}

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        stories = output.get("stories") or []
        if not stories:
            return []
        industry = output.get("industry") or "the category"
        intro = f"Industry pulse — recent {industry} stories:"
        blocks: list[dict[str, Any]] = [crm.paragraph(intro)]
        for s in stories:
            headline = s.get("headline", "?")
            blocks.append(crm.bullet(headline))
        return blocks

    def to_signal_sections(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        if not output.get("industry_movement_detected"):
            return []
        stories = output.get("stories") or []
        movement_stories = [
            s for s in stories if s.get("buying_implication") == "industry movement"
        ]
        if not movement_stories:
            movement_stories = stories
        industry = output.get("industry") or "the category"
        bullets = "; ".join(
            s.get("headline", "") for s in movement_stories if s.get("headline")
        )
        logic = (
            f"Category-level shift detected in {industry}. "
            f"Recent stories suggesting consolidation, AI disruption, or "
            f"structural change that affects buying behaviour: {bullets}."
        )
        sources = [
            s.get("url") for s in movement_stories if s.get("url")
        ] or list(output.get("sources") or [])
        return [{"signal": "industry movement", "logic": logic, "sources": sources}]
