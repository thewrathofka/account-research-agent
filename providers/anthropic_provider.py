"""Anthropic provider — wraps the anthropic SDK in the LLMProvider protocol.

Extracts the current ReAct loop from the v0.0 single-file MVP.
"""

from __future__ import annotations

from typing import Any

from anthropic import Anthropic

import config
from providers.base import LLMProvider, ProviderResult
from tools.base import Tool


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str | None = None, max_retries: int = 8):
        self.model = model or config.ANTHROPIC_MODEL
        self.client = Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=max_retries)

    def run_loop(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[Tool],
        max_iterations: int = 10,
        tool_call_cap: int | None = None,
    ) -> ProviderResult:
        tool_specs = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        tool_by_name = {t.name: t for t in tools}

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        input_tokens = 0
        output_tokens = 0
        tool_calls_made = 0

        try:
            for iteration in range(1, max_iterations + 1):
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=config.MAX_TOKENS,
                    system=system_prompt,
                    tools=tool_specs,
                    messages=messages,
                )
                input_tokens += response.usage.input_tokens
                output_tokens += response.usage.output_tokens
                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason == "end_turn":
                    text = "".join(b.text for b in response.content if b.type == "text")
                    return ProviderResult(
                        text=text,
                        tool_calls_made=tool_calls_made,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        iterations=iteration,
                        stop_reason="end_turn",
                    )

                if response.stop_reason == "tool_use":
                    if tool_call_cap is not None and tool_calls_made >= tool_call_cap:
                        return ProviderResult(
                            text="", tool_calls_made=tool_calls_made,
                            input_tokens=input_tokens, output_tokens=output_tokens,
                            iterations=iteration, stop_reason="tool_cap",
                            error=f"hit tool_call_cap ({tool_call_cap})",
                        )
                    tool_results = []
                    for block in response.content:
                        if block.type != "tool_use":
                            continue
                        tool_calls_made += 1
                        tool = tool_by_name.get(block.name)
                        result = (tool(**block.input)
                                  if tool else f"Unknown tool: {block.name}")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                    messages.append({"role": "user", "content": tool_results})
                    continue

                return ProviderResult(
                    text="", tool_calls_made=tool_calls_made,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    iterations=iteration, stop_reason="error",
                    error=f"unexpected stop_reason: {response.stop_reason}",
                )

            return ProviderResult(
                text="", tool_calls_made=tool_calls_made,
                input_tokens=input_tokens, output_tokens=output_tokens,
                iterations=max_iterations, stop_reason="max_iter",
                error=f"hit max_iterations ({max_iterations})",
            )
        except Exception as e:
            return ProviderResult(
                text="", tool_calls_made=tool_calls_made,
                input_tokens=input_tokens, output_tokens=output_tokens,
                iterations=0, stop_reason="error",
                error=f"{type(e).__name__}: {e}",
            )
