"""Module 14 — Hiring/downsizing signal.

Tools: hiring_signals (jobspy) + web_search (Tavily).
Output: Buying Signals multi-select + Headcount page sub-section under Overview.
"""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_14_hiring_signal as prompt
from tasks.base import Task
from tools.base import Tool
from tools.hiring_signals import HiringSignalsTool
from tools.web_search import WebSearchTool


class Module14HiringSignal(Task):
    name = "module_14_hiring_signal"
    section = "Overview"
    subsection = "Headcount"
    prompt_module = prompt

    def build_tools(self) -> list[Tool]:
        return [HiringSignalsTool(), WebSearchTool()]

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        signal = output.get("headcount_signal")
        if signal in ("hiring", "downsizing"):
            return {
                crm.PROP_BUYING_SIGNALS: {
                    "multi_select": [{"name": signal}],
                }
            }
        return {}

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        summary = output.get("headcount_summary")
        if not summary:
            return []
        return [crm.paragraph(summary)]
