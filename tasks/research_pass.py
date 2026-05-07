"""ResearchPass task — runs once per account after gate, before synthesis modules.

This task does NOT write to Notion (no fields, no page blocks). It populates
the orchestrator's context envelope with raw_research + sources, which
synthesis modules then read in their build_user_message().
"""

from __future__ import annotations

from typing import Any

import prompts.research_pass as prompt
from tasks.base import Task
from tools.base import Tool
from tools import build_search_tool


class ResearchPass(Task):
    name = "research_pass"
    section = "Overview"   # not actually rendered — to_blocks returns []
    subsection = None
    prompt_module = prompt
    model_tier = "smart"   # broad research benefits from Sonnet's reasoning

    def build_tools(self) -> list[Tool]:
        return [build_search_tool()]

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        return {}  # no Notion property writes

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        return []  # not rendered to the page body
