"""Module 10 — Ad library presence across LinkedIn / Meta / TikTok.

Tool-using task (not synthesis-only). Calls apify_ad_scraper one platform at
a time so the gating decisions (B2C → Meta? Gen-Z → TikTok?) are visible per
call in the agent loop and per call in the run log.

Outputs:
- No Notion properties — page body only.
- Page-body section "Creative Posture → Ads Running" with per-platform
  bullets: format mix + volume bucket + brief note.
"""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_10_ad_library as prompt
from tasks.base import Task, _today_header
from tools.apify_ad_scraper import ApifyAdScraperTool
from tools.base import Tool


class Module10AdLibrary(Task):
    name = "module_10_ad_library"
    section = "Creative Posture"
    subsection = "Ads Running"
    prompt_module = prompt

    def build_tools(self) -> list[Tool]:
        return [ApifyAdScraperTool()]

    def build_user_message(self, account_name: str, context: dict[str, Any]) -> str:
        """Inject the research-pass classification cues so the model doesn't waste
        a tool call deciding if the company is B2B vs B2C."""
        research = (context.get("research_pass") or {}).get("raw_research", "")
        revenue = context.get("module_03_revenue_model") or {}
        creative = context.get("module_09_creative_reality") or {}

        prior_lines: list[str] = []
        segment = revenue.get("primary_customer_segment")
        if segment:
            prior_lines.append(f"Primary customer segment: {segment}")
        agencies = creative.get("named_agencies") or []
        if agencies:
            prior_lines.append(f"Known agency partners: {', '.join(agencies)}")

        prior_block = ""
        if prior_lines:
            prior_block = "Prior research established:\n" + "\n".join(
                f"- {line}" for line in prior_lines
            ) + "\n\n"

        # Trim research context to keep the user-message size sane — the model
        # doesn't need the full 4-5k token raw_research dump to classify
        # audience; the first ~1500 chars cover overview + customer segment.
        research_excerpt = research[:1500] if research else "(no research context)"

        return (
            f"{_today_header()}\n\n"
            f"Company: {account_name}.\n\n"
            f"{prior_block}"
            f"## Research context (excerpt — first 1500 chars)\n\n"
            f"{research_excerpt}\n\n"
            "Workflow:\n"
            "1. Classify the audience (B2B/B2C/hybrid + Gen-Z/lifestyle?) from "
            "the context above.\n"
            "2. Call apify_ad_scraper(platform='linkedin', company=<brand>).\n"
            "3. Based on classification AND LinkedIn count, decide whether to "
            "also call meta and/or tiktok per the gating rules.\n"
            "4. Output the JSON schema. Always include all 3 platforms in the "
            "platforms array (use ads_running=0 + 'not applicable' note when "
            "skipping).\n"
        )

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        return {}  # page-body only

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        platforms = output.get("platforms") or []
        if not platforms:
            return []

        blocks: list[dict[str, Any]] = []
        for p in platforms:
            name = p.get("platform", "?")
            count = p.get("ads_running", 0)
            volume = p.get("volume", "none")
            note = p.get("note") or ""
            fmt_mix = p.get("format_mix") or []

            if count == 0 and volume == "none":
                blocks.append(crm.bullet(f"{name}: no active ads. {note}".strip()))
                continue

            fmt_summary = ", ".join(
                f"{f.get('count', 0)} {f.get('format', '?')}" for f in fmt_mix
            ) if fmt_mix else "format mix unavailable"
            head = f"{name}: {count} ads ({volume} volume) — {fmt_summary}."
            if note:
                head += f" {note}"
            blocks.append(crm.bullet(head))

        return blocks
