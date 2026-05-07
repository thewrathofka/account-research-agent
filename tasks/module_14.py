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
from tools import build_search_tool


class Module14HiringSignal(Task):
    name = "module_14_hiring_signal"
    section = "Overview"
    subsection = "Headcount"
    prompt_module = prompt

    def build_tools(self) -> list[Tool]:
        return [HiringSignalsTool(), build_search_tool()]

    def build_user_message(self, account_name: str, context: dict[str, Any]) -> str:
        """Use gate output (size_band) to skip headcount-confirmation searches."""
        gate = context.get("module_01_gate")
        news = context.get("module_06_structural_news")
        prior = []
        if gate and gate.get("size_band"):
            prior.append(f"company size confirmed at {gate['size_band']} employees")
        if news and news.get("structure_note") == "mass layoffs":
            prior.append(f"recent layoff event already detected: {news.get('event_summary','')}")
        if not prior:
            return f"Research the company: {account_name}"
        prior_str = "; ".join(prior)
        return (
            f"Research the company: {account_name}.\n"
            f"Prior research established: {prior_str}. "
            f"Skip re-confirming size; focus your searches on active hiring "
            f"(creative/marketing roles) and very-recent layoff news."
        )

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
