"""OpenAI provider — wraps the openai SDK Chat Completions tool-use loop.

Supports model tiers (smart=gpt-4.1, fast=gpt-4.1-mini). OpenAI auto-caches
system prompts above 1024 tokens; supports_caching=True is purely informational
(no cache_control hint needed).
"""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

import config
from providers.base import LLMProvider, ProviderResult
from tools.base import Tool


class OpenAIProvider(LLMProvider):
    name = "openai"
    models = config.OPENAI_MODELS
    supports_caching = True  # auto-caches above 1024 tokens — no hint needed

    def __init__(self, max_retries: int = 8):
        self.model = self.models["smart"]
        self.client = OpenAI(api_key=config.OPENAI_API_KEY, max_retries=max_retries)

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
        cache_system_prompt: bool = True,  # OpenAI auto-caches; flag is informational
    ) -> ProviderResult:
        model = self._resolve_model(model_tier)
        tool_specs = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]
        tool_by_name = {t.name: t for t in tools}

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        input_tokens = 0
        output_tokens = 0
        cached_input_tokens = 0
        tool_calls_made = 0

        try:
            for iteration in range(1, max_iterations + 1):
                response = self.client.chat.completions.create(
                    model=model,
                    max_tokens=config.MAX_TOKENS,
                    messages=messages,
                    tools=tool_specs,
                )
                if response.usage:
                    input_tokens += response.usage.prompt_tokens
                    output_tokens += response.usage.completion_tokens
                    # OpenAI surfaces cached tokens in prompt_tokens_details.cached_tokens
                    details = getattr(response.usage, "prompt_tokens_details", None)
                    if details is not None:
                        cached_input_tokens += getattr(details, "cached_tokens", 0) or 0

                choice = response.choices[0]
                msg = choice.message
                messages.append(_assistant_message_to_dict(msg))

                if choice.finish_reason == "stop":
                    return ProviderResult(
                        text=msg.content or "",
                        tool_calls_made=tool_calls_made,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        cached_input_tokens=cached_input_tokens,
                        iterations=iteration, stop_reason="end_turn",
                        model_used=model,
                    )

                if choice.finish_reason == "tool_calls" and msg.tool_calls:
                    if tool_call_cap is not None and tool_calls_made >= tool_call_cap:
                        return ProviderResult(
                            text="", tool_calls_made=tool_calls_made,
                            input_tokens=input_tokens, output_tokens=output_tokens,
                            cached_input_tokens=cached_input_tokens,
                            iterations=iteration, stop_reason="tool_cap",
                            error=f"hit tool_call_cap ({tool_call_cap})",
                            model_used=model,
                        )
                    for tc in msg.tool_calls:
                        tool_calls_made += 1
                        tool = tool_by_name.get(tc.function.name)
                        try:
                            tool_input = json.loads(tc.function.arguments or "{}")
                        except json.JSONDecodeError:
                            tool_input = {}
                        result = (tool(**tool_input)
                                  if tool else f"Unknown tool: {tc.function.name}")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })
                    continue

                return ProviderResult(
                    text=msg.content or "",
                    tool_calls_made=tool_calls_made,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                    iterations=iteration, stop_reason="error",
                    error=f"unexpected finish_reason: {choice.finish_reason}",
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


def _assistant_message_to_dict(msg: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"role": "assistant", "content": msg.content}
    if msg.tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]
    return out
