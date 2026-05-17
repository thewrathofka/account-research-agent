"""Module 9 — Top 3 competitors. Output → Competitor Landscape page section."""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_09_competitor_snapshot as prompt
from tasks.base import Task


class Module09CompetitorSnapshot(Task):
    name = "module_09_competitor_snapshot"
    section = "Competitor Landscape"
    subsection = None
    prompt_module = prompt
    model_tier = "fast"  # competitor lookup is straightforward; Haiku/mini handles fine
    synthesis_only = True   # reads ResearchPass output, no own tools

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        return {}  # page-body only

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        competitors = output.get("competitors") or []
        if not competitors:
            return [crm.paragraph("No clear direct competitors identified.")]
        names = [c.get("name", "?") for c in competitors]
        intro = f"Top direct competitors: {', '.join(names)}."
        blocks: list[dict[str, Any]] = [crm.paragraph(intro)]
        for c in competitors:
            blocks.append(crm.bullet(
                f"{c.get('name', '?')}: {c.get('positioning_differentiator', '')}"
            ))
        return blocks
