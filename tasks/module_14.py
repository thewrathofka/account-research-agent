"""Module 14 — Hiring/downsizing signal.

Tools: hiring_signals (jobspy) + web_search (Tavily).
Output: Buying Signals multi-select + Headcount page sub-section under Overview.
"""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_14_hiring_signal as prompt
from tasks.base import Task, _today_header
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
        """Inject gate output so the model skips redundant searches AND uses
        region-aware hiring queries (Fix Appendix #12)."""
        gate = context.get("module_01_gate") or {}
        news = context.get("module_06_structural_news") or {}
        prior: list[str] = []

        emp = gate.get("employee_count_estimate")
        if isinstance(emp, int):
            prior.append(f"company size confirmed at ~{emp:,} employees")

        regions = gate.get("regions_present") or []
        if not regions:
            # Fall back to a sane default when the gate didn't surface countries.
            if gate.get("operates_in_na"):
                regions = ["USA"]
            elif gate.get("operates_in_eu"):
                regions = ["UK", "Germany"]
        regions_str = ", ".join(regions) if regions else "USA"

        if news.get("structure_note") == "mass layoffs":
            prior.append(f"recent layoff event already detected: {news.get('event_summary','')}")

        prior_block = ("Prior research established: " + "; ".join(prior) + ". ") if prior else ""
        return (
            f"{_today_header()}\n\n"
            f"Research the company: {account_name}.\n"
            f"{prior_block}"
            f"When you call hiring_signals, pass countries={regions} to scrape "
            f"each market the company operates in ({regions_str}). "
            f"Focus your searches on active hiring (creative/marketing roles) "
            f"and very-recent layoff news (call web_search with days=90, fall "
            f"back to days=180 only if no 90-day result)."
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

    def to_signal_sections(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        signal = output.get("headcount_signal")
        if signal not in ("hiring", "downsizing"):
            return []
        if signal == "hiring":
            roles = output.get("creative_marketing_roles_count")
            total = output.get("active_open_roles_total")
            titles = output.get("creative_marketing_role_titles") or []
            titles_blurb = (
                f" Notable open roles: {', '.join(titles[:5])}." if titles else ""
            )
            logic = (
                f"Active hiring detected. "
                f"{roles} creative/marketing role(s) open out of {total} total "
                f"across the company's operating regions — above the threshold "
                f"(>=3) that flags a hiring posture.{titles_blurb}"
            )
        else:  # downsizing
            layoff_summary = output.get("layoff_summary") or output.get("headcount_summary") or ""
            logic = (
                f"Downsizing detected within the last 6 months. {layoff_summary}"
            )
        sources = list(output.get("sources") or [])
        return [{"signal": signal, "logic": logic, "sources": sources}]


