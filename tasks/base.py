"""Task framework — one Task per research question.

Each Task subclass declares:
  - name:           short identifier (used in CLI + run log)
  - section:        which page-body section its blocks land under
                    (e.g. "Overview", "News", "Creative Posture", ...)
  - subsection:     optional sub-heading inside the section (e.g. "Headcount")
  - prompt_module:  the prompts/ module providing VERSION + SYSTEM_PROMPT
  - tools:          list of Tool factories used during the ReAct loop

Subclasses override:
  - to_fields(output): map model output -> Notion property update payload
  - to_blocks(output): map model output -> Notion children blocks for the page body
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Callable

from providers.base import LLMProvider
from tools.base import Tool


@dataclass
class TaskResult:
    task_name: str
    output: dict[str, Any] | None       # parsed JSON from the agent
    confidence: str                     # "high" | "medium" | "low" | "failed"
    fields: dict[str, Any]              # Notion property updates this task produced
    page_blocks: list[dict[str, Any]]   # Notion blocks to append to the page body
    section: str                        # which section these blocks go under
    subsection: str | None              # optional sub-section heading
    sources: list[str]
    search_count: int
    input_tokens: int
    output_tokens: int
    duration_seconds: float
    prompt_version: str
    provider_name: str
    error: str | None = None


class Task:
    """A single research question. Subclasses set the class-level attributes below."""

    name: str = ""
    section: str = "Overview"
    subsection: str | None = None
    prompt_module: ModuleType | None = None  # set in subclass: `import prompts.module_NN as ...`

    def build_tools(self) -> list[Tool]:
        """Return fresh Tool instances for this run (per-task call counters)."""
        return []

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        return {}

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    # ---- run loop ----

    def run(self, account_name: str, provider: LLMProvider) -> TaskResult:
        if self.prompt_module is None:
            raise RuntimeError(f"Task {self.name!r} has no prompt_module set.")

        system_prompt = self.prompt_module.SYSTEM_PROMPT
        prompt_version = getattr(self.prompt_module, "VERSION", "unknown")
        tools = self.build_tools()

        start = time.time()
        result = provider.run_loop(
            system_prompt=system_prompt,
            user_message=f"Research the company: {account_name}",
            tools=tools,
        )
        duration = time.time() - start

        if result.error or result.stop_reason != "end_turn":
            return TaskResult(
                task_name=self.name, output=None, confidence="failed",
                fields={}, page_blocks=[], section=self.section, subsection=self.subsection,
                sources=[],
                search_count=sum(t.call_count for t in tools),
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                duration_seconds=duration,
                prompt_version=prompt_version, provider_name=provider.name,
                error=result.error or f"stop_reason: {result.stop_reason}",
            )

        output = _extract_json(result.text)
        if output is None:
            return TaskResult(
                task_name=self.name, output=None, confidence="failed",
                fields={}, page_blocks=[], section=self.section, subsection=self.subsection,
                sources=[],
                search_count=sum(t.call_count for t in tools),
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                duration_seconds=duration,
                prompt_version=prompt_version, provider_name=provider.name,
                error=f"end_turn without JSON block:\n{result.text[:500]}",
            )

        confidence = output.get("confidence", "low")
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"

        return TaskResult(
            task_name=self.name, output=output, confidence=confidence,
            fields=self.to_fields(output), page_blocks=self.to_blocks(output),
            section=self.section, subsection=self.subsection,
            sources=output.get("sources", []) or [],
            search_count=sum(t.call_count for t in tools),
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            duration_seconds=duration,
            prompt_version=prompt_version, provider_name=provider.name,
        )


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull a JSON object out of a ```json fenced block, falling back to the first
    parseable {...} substring if no fence is found."""
    fenced = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            return None
    for match in re.finditer(r"\{.*?\}", text, re.DOTALL):
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
    return None
