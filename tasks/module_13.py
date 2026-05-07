"""Module 13 — Industry pulse (last 60 days).

Coupled to module 7's Buying Intent: when industry_movement_detected=true, this
module appends `industry movement` to that multi-select. Output also lands in
the Competitor Landscape section as a paragraph.
"""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_13_industry_pulse as prompt
from tasks.base import Task
from tools.base import Tool
from tools import build_search_tool


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
                crm.PROP_BUYING_INTENT: {
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
