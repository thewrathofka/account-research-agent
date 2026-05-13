"""ResearchPass task — runs once per account after gate, before synthesis modules.

This task does NOT write to Notion (no fields, no page blocks). It populates
the orchestrator's context envelope with raw_research + sources, which
synthesis modules then read in their build_user_message().
"""

from __future__ import annotations

import re
from typing import Any

import prompts.research_pass as prompt
from tasks.base import Task
from tools.base import Tool
from tools import build_search_tool


# Delimiter pair for the raw_research prose block. Pulled OUT of the JSON in
# v2.0.0 (2026-05-13) — embedding 1500-3000 words of markdown inside a JSON
# string field caused unrecoverable parser failures when the model emitted an
# unescaped `"` mid-prose (Mendix #706, Roblox #557 on 2026-05-12). The
# delimited block contains arbitrary prose; the JSON only carries small,
# escape-safe fields.
_RAW_OPEN = "<<<RAW_RESEARCH>>>"
_RAW_CLOSE = "<<<END_RAW_RESEARCH>>>"
_RAW_BLOCK_RE = re.compile(
    rf"{re.escape(_RAW_OPEN)}\s*(.*?)\s*{re.escape(_RAW_CLOSE)}",
    re.DOTALL,
)


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

    def post_parse(self, raw_text: str, output: dict[str, Any]) -> dict[str, Any]:
        """Inject the delimited raw_research prose block into the parsed dict.

        Three cases, in order:
          1. Delimited block present in raw_text → use it (canonical v2.0.0 path).
          2. Backwards-compat: model used the v1.x inline format (raw_research
             already in `output`) and JSON happened to parse → keep as-is.
          3. Neither → set raw_research="" so downstream synthesis tasks can
             still proceed (they degrade with confidence=low, see
             Task.synthesis_user_message fallback).
        """
        match = _RAW_BLOCK_RE.search(raw_text)
        if match:
            output["raw_research"] = match.group(1)
        elif "raw_research" not in output or output.get("raw_research") is None:
            output["raw_research"] = ""
        return output
