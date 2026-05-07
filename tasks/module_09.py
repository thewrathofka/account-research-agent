"""Module 9 — Creative reality (lite). Output → Creative Posture page section."""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_09_creative_reality as prompt
from tasks.base import Task
from tools.base import Tool
from tools.hiring_signals import HiringSignalsTool
from tools.web_search import WebSearchTool


class Module09CreativeReality(Task):
    name = "module_09_creative_reality"
    section = "Creative Posture"
    subsection = None
    prompt_module = prompt

    def build_tools(self) -> list[Tool]:
        return [HiringSignalsTool(), WebSearchTool()]

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        return {}  # page-body only

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        summary = output.get("creative_posture_summary")
        if not summary:
            return []
        blocks: list[dict[str, Any]] = [crm.paragraph(summary)]

        agencies = output.get("named_agencies") or []
        if agencies:
            blocks.append(crm.bullet(f"Named agency partners: {', '.join(agencies)}"))

        phrases = output.get("jd_pain_phrases") or []
        if phrases:
            blocks.append(crm.paragraph("Pain phrases from active JDs:"))
            for p in phrases:
                blocks.append(crm.bullet(f'"{p}"'))

        return blocks
