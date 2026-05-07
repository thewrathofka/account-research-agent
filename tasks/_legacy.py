"""Legacy v0.0 CompanyOverview task — kept until Phase 1 modules replace it.

This is the placeholder task we used for the Oracle live test. Producing structured
output the orchestrator can verify end-to-end, even before real prompts ship.
"""

from __future__ import annotations

from typing import Any

import crm
import prompts._legacy as legacy_prompt
from tasks.base import Task
from tools.base import Tool
from tools import build_search_tool


class CompanyOverview(Task):
    name = "company_overview"
    section = "Overview"
    subsection = None
    prompt_module = legacy_prompt

    def build_tools(self) -> list[Tool]:
        return [build_search_tool()]

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        size = output.get("size_band")
        if size in crm.SIZE_OPTIONS:
            fields[crm.PROP_SIZE] = {"select": {"name": size}}
        if output.get("is_hiring_creatives"):
            fields[crm.PROP_BUYING_SIGNALS] = {"multi_select": [{"name": "hiring"}]}
        return fields

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if output.get("summary"):
            blocks.append(crm.paragraph(output["summary"]))
        for label, key in [
            ("Industry", "industry"),
            ("Size", "size_band"),
            ("HQ", "hq_country"),
            ("Hiring creatives", "is_hiring_creatives"),
        ]:
            value = output.get(key)
            if value is None or value == "":
                continue
            blocks.append(crm.bullet(f"{label}: {value}"))
        if output.get("notes"):
            blocks.append(crm.bullet(f"Notes: {output['notes']}"))
        return blocks
