"""OpenAI provider — wraps the openai SDK Chat Completions tool-use loop.

Translates the provider-neutral Tool list into OpenAI's `tools` schema and the
`tool_calls` / `tool` role response format. Token usage is normalized:
prompt_tokens → input_tokens, completion_tokens → output_tokens.

This implementation is the *proof* of the seamless-swap claim: it should produce
schema-compliant output for the exact same prompts AnthropicProvider runs.
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

    def __init__(self, model: str | None = None, max_retries: int = 8):
        self.model = model or config.OPENAI_MODEL
        self.client = OpenAI(api_key=config.OPENAI_API_KEY, max_retries=max_retries)

    def run_loop(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[Tool],
        max_iterations: int = 10,
        tool_call_cap: int | None = None,
    ) -> ProviderResult:
        # OpenAI tool spec uses {"type": "function", "function": {...}}
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

        # OpenAI puts system prompt as a regular message with role=system.
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        input_tokens = 0
        output_tokens = 0
        tool_calls_made = 0

        try:
            for iteration in range(1, max_iterations + 1):
                response = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=config.MAX_TOKENS,
                    messages=messages,
                    tools=tool_specs,
                )
                # OpenAI usage field names differ from Anthropic.
                if response.usage:
                    input_tokens += response.usage.prompt_tokens
                    output_tokens += response.usage.completion_tokens

                choice = response.choices[0]
                msg = choice.message
                # Append the assistant turn verbatim — OpenAI requires the same
                # tool_calls structure echoed back when we send tool results.
                messages.append(_assistant_message_to_dict(msg))

                if choice.finish_reason == "stop":
                    text = msg.content or ""
                    return ProviderResult(
                        text=text,
                        tool_calls_made=tool_calls_made,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        iterations=iteration,
                        stop_reason="end_turn",
                    )

                if choice.finish_reason == "tool_calls" and msg.tool_calls:
                    if tool_call_cap is not None and tool_calls_made >= tool_call_cap:
                        return ProviderResult(
                            text="", tool_calls_made=tool_calls_made,
                            input_tokens=input_tokens, output_tokens=output_tokens,
                            iterations=iteration, stop_reason="tool_cap",
                            error=f"hit tool_call_cap ({tool_call_cap})",
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
                        # Each tool result is its own message with role="tool"
                        # and a matching tool_call_id.
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })
                    continue

                # Other finish_reasons (length, content_filter, etc.) — bail.
                return ProviderResult(
                    text=msg.content or "",
                    tool_calls_made=tool_calls_made,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    iterations=iteration, stop_reason="error",
                    error=f"unexpected finish_reason: {choice.finish_reason}",
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


def _assistant_message_to_dict(msg: Any) -> dict[str, Any]:
    """Convert the SDK's ChatCompletionMessage to a plain dict the API will accept
    when echoed back as conversation history."""
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
