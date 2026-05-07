"""Module 6 — Structural news (last 6 months). Output → Structure Notes property + News page section."""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_06_structural_news as prompt
from tasks.base import Task
from tools.base import Tool
from tools import build_search_tool


class Module06StructuralNews(Task):
    name = "module_06_structural_news"
    section = "News"
    subsection = None
    prompt_module = prompt
    model_tier = "fast"  # news lookup; Haiku/mini handles fine

    def build_tools(self) -> list[Tool]:
        return [build_search_tool()]

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
