"""Anthropic provider — wraps the anthropic SDK in the LLMProvider protocol.

Supports model tiers (smart=Sonnet, fast=Haiku) and explicit prompt caching
via cache_control: ephemeral on the system prompt block.
"""

from __future__ import annotations

from typing import Any

from anthropic import Anthropic

import config
from providers.base import LLMProvider, ProviderResult
from tools.base import Tool


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    models = config.ANTHROPIC_MODELS
    supports_caching = True

    def __init__(self, max_retries: int = 8):
        self.model = self.models["smart"]
        self.client = Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=max_retries)

    def _resolve_model(self, model_tier: str) -> str:
        return self.models.get(model_tier, self.models["smart"])

    def run_loop(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[Tool],
        max_iterations: int = 10,
        tool_call_cap: int | None = None,
        model_tier: str = "smart",
        cache_system_prompt: bool = True,
    ) -> ProviderResult:
        model = self._resolve_model(model_tier)
        tool_specs = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        tool_by_name = {t.name: t for t in tools}

        # System block — wrap in a list so we can attach cache_control to it.
        if cache_system_prompt:
            system_blocks = [{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }]
        else:
            system_blocks = system_prompt  # raw str works too

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        input_tokens = 0
        output_tokens = 0
        cached_input_tokens = 0
        tool_calls_made = 0

        try:
            for iteration in range(1, max_iterations + 1):
                response = self.client.messages.create(
                    model=model,
                    max_tokens=config.MAX_TOKENS,
                    system=system_blocks,
                    tools=tool_specs,
                    messages=messages,
                )
                input_tokens += response.usage.input_tokens
                output_tokens += response.usage.output_tokens
                # Track cache hits for cost telemetry.
                cached_input_tokens += getattr(response.usage, "cache_read_input_tokens", 0) or 0
                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason == "end_turn":
                    text = "".join(b.text for b in response.content if b.type == "text")
                    return ProviderResult(
                        text=text, tool_calls_made=tool_calls_made,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        cached_input_tokens=cached_input_tokens,
                        iterations=iteration, stop_reason="end_turn",
                        model_used=model,
                    )

                if response.stop_reason == "tool_use":
                    if tool_call_cap is not None and tool_calls_made >= tool_call_cap:
                        return ProviderResult(
                            text="", tool_calls_made=tool_calls_made,
                            input_tokens=input_tokens, output_tokens=output_tokens,
                            cached_input_tokens=cached_input_tokens,
                            iterations=iteration, stop_reason="tool_cap",
                            error=f"hit tool_call_cap ({tool_call_cap})",
                            model_used=model,
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
                    cached_input_tokens=cached_input_tokens,
                    iterations=iteration, stop_reason="error",
                    error=f"unexpected stop_reason: {response.stop_reason}",
                    model_used=model,
                )

            return ProviderResult(
                text="", tool_calls_made=tool_calls_made,
                input_tokens=input_tokens, output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                iterations=max_iterations, stop_reason="max_iter",
                error=f"hit max_iterations ({max_iterations})",
                model_used=model,
            )
        except Exception as e:
            return ProviderResult(
                text="", tool_calls_made=tool_calls_made,
                input_tokens=input_tokens, output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                iterations=0, stop_reason="error",
                error=f"{type(e).__name__}: {e}",
                model_used=model,
            )
