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

import config
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
    cached_input_tokens: int = 0        # tokens served from prompt cache
    model_used: str | None = None        # provider-specific model ("claude-haiku-4-5" etc.)
    error: str | None = None


class Task:
    """A single research question. Subclasses set the class-level attributes below."""

    name: str = ""
    section: str = "Overview"
    subsection: str | None = None
    prompt_module: ModuleType | None = None  # set in subclass: `import prompts.module_NN as ...`
    model_tier: str = "smart"   # "smart" (default Sonnet/gpt-4.1) | "fast" (Haiku/mini)
    cache_system_prompt: bool = True   # toggle caching per-task if needed

    # If True, this task does NOT use tools or run an agent loop. It receives the
    # ResearchPass output via context and produces its structured JSON in a single
    # LLM call. Use for tasks whose source material is fully derivable from the
    # shared research context. Saves ~50-70% per task vs the full agent loop.
    synthesis_only: bool = False

    def build_tools(self) -> list[Tool]:
        """Return fresh Tool instances for this run (per-task call counters)."""
        return []

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        return {}

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def build_user_message(self, account_name: str, context: dict[str, Any]) -> str:
        """Default: just ask the model to research the company. Subclasses override
        to inject prior-task context, which lets the agent skip redundant searches.

        Synthesis-only tasks use the helper below to fold ResearchPass output in.
        """
        if self.synthesis_only:
            return self.synthesis_user_message(account_name, context)
        return f"Research the company: {account_name}"

    def synthesis_user_message(self, account_name: str, context: dict[str, Any]) -> str:
        """Build a synthesis-only user message that embeds the shared research context.
        Override in subclasses if a task needs to also reference earlier synthesis outputs."""
        research = (context.get("research_pass") or {}).get("raw_research", "")
        sources = (context.get("research_pass") or {}).get("sources", [])
        sources_block = "\n".join(f"- {u}" for u in sources) if sources else "(no sources captured)"
        if not research:
            # Fallback if research_pass didn't run — model has to do its own searches
            # via tools (but synthesis_only=True means it has no tools). Surface this
            # clearly so the model returns confidence=low rather than hallucinating.
            return (
                f"Company: {account_name}.\n\n"
                "No upfront research context is available. Return your best-effort "
                "answer based on general knowledge, but set confidence='low' and "
                "leave fields null where you cannot verify."
            )
        return (
            f"Company: {account_name}.\n\n"
            f"## Research context (from upfront research pass)\n\n"
            f"{research}\n\n"
            f"## Sources\n{sources_block}\n\n"
            "Using ONLY the research context above, fill out your task's JSON schema. "
            "Do not invent facts that aren't present in the research context — set "
            "fields to null and lower confidence if the research doesn't cover them. "
            "The 'sources' field in your output must be a subset of the URLs above."
        )

    # ---- run loop ----

    def run(
        self, account_name: str, provider: LLMProvider,
        context: dict[str, Any] | None = None,
    ) -> TaskResult:
        if self.prompt_module is None:
            raise RuntimeError(f"Task {self.name!r} has no prompt_module set.")

        system_prompt = self.prompt_module.SYSTEM_PROMPT
        prompt_version = getattr(self.prompt_module, "VERSION", "unknown")
        ctx = context or {}

        # Build the user message. If `context` contains relevant prior task outputs
        # this task can use, the subclass overrides build_user_message() to inject
        # them (saves search iterations).
        user_message = self.build_user_message(account_name, ctx)

        # Synthesis-only tasks skip tools entirely — single LLM call, no agent loop.
        # The shared research context is folded into user_message via the subclass's
        # build_user_message() override.
        if self.synthesis_only:
            tools: list[Tool] = []
            max_iter = 1
        else:
            tools = self.build_tools()
            max_iter = config.MAX_AGENT_ITERATIONS

        start = time.time()
        result = provider.run_loop(
            system_prompt=system_prompt,
            user_message=user_message,
            tools=tools,
            max_iterations=max_iter,
            model_tier=self.model_tier,
            cache_system_prompt=self.cache_system_prompt,
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
                cached_input_tokens=result.cached_input_tokens,
                model_used=result.model_used,
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
                cached_input_tokens=result.cached_input_tokens,
                model_used=result.model_used,
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
            cached_input_tokens=result.cached_input_tokens,
            model_used=result.model_used,
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
