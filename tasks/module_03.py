"""Module 3 — How they make money. Output → page-body Overview section."""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_03_revenue_model as prompt
from tasks.base import Task
from tools.base import Tool
from tools import build_search_tool


class Module03RevenueModel(Task):
    name = "module_03_revenue_model"
    section = "Overview"
    subsection = None
    prompt_module = prompt
    synthesis_only = True   # reads ResearchPass output, no own tools

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        return {}  # this module writes only to the page body, not to properties

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if output.get("summary"):
            blocks.append(crm.paragraph(output["summary"]))
        return blocks
