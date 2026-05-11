"""Anthropic provider — wraps the anthropic SDK in the LLMProvider protocol.

Supports model tiers (smart=Sonnet, fast=Haiku) and explicit prompt caching
via cache_control: ephemeral on the system prompt block. Also supports the
Anthropic Message Batches API for ~50% off batch processing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from anthropic import Anthropic

import config
from providers.base import BatchHandle, BatchRequest, LLMProvider, ProviderResult
from rate_limit import ANTHROPIC_LIMITER
from tools.base import Tool


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    models = config.ANTHROPIC_MODELS
    supports_caching = True
    supports_batch = True

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
                with ANTHROPIC_LIMITER:
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

    # ---- Batch API ----

    def submit_batch(self, requests: list[BatchRequest]) -> BatchHandle:
        """Submit a Message Batch via Anthropic's Message Batches API.

        Each BatchRequest becomes one message-create request. NO tool use is
        supported in batch mode (synthesis tasks only). System prompts get
        cache_control: ephemeral (helps when many requests share the same prompt).
        """
        from anthropic.types.messages.batch_create_params import Request

        batch_requests = []
        for r in requests:
            model = self._resolve_model(r.model_tier)
            batch_requests.append(Request(
                custom_id=r.custom_id,
                params={
                    "model": model,
                    "max_tokens": r.max_tokens or config.MAX_TOKENS,
                    "system": [{
                        "type": "text",
                        "text": r.system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }],
                    "messages": [{"role": "user", "content": r.user_message}],
                },
            ))
        with ANTHROPIC_LIMITER:
            batch = self.client.messages.batches.create(requests=batch_requests)
        return BatchHandle(
            batch_id=batch.id,
            provider_name=self.name,
            submitted_at=datetime.now(timezone.utc).isoformat(),
            request_count=len(requests),
            expected_status=_normalize_batch_status(batch.processing_status),
        )

    def poll_batch(self, handle: BatchHandle) -> str:
        """Return normalized status: "in_progress" | "ended" | "errored"."""
        with ANTHROPIC_LIMITER:
            batch = self.client.messages.batches.retrieve(message_batch_id=handle.batch_id)
        return _normalize_batch_status(batch.processing_status)

    def fetch_batch_results(self, handle: BatchHandle) -> dict[str, ProviderResult]:
        """Pull results JSONL stream and parse into per-custom_id ProviderResults."""
        results: dict[str, ProviderResult] = {}
        with ANTHROPIC_LIMITER:
            entries = list(self.client.messages.batches.results(message_batch_id=handle.batch_id))
        for entry in entries:
            custom_id = entry.custom_id
            res = entry.result
            if res.type == "succeeded":
                msg = res.message
                text = "".join(b.text for b in msg.content if b.type == "text")
                results[custom_id] = ProviderResult(
                    text=text, tool_calls_made=0,
                    input_tokens=msg.usage.input_tokens,
                    output_tokens=msg.usage.output_tokens,
                    cached_input_tokens=getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
                    iterations=1, stop_reason="end_turn",
                    model_used=msg.model,
                )
            elif res.type == "errored":
                err = getattr(res, "error", None)
                err_msg = getattr(err, "message", str(err)) if err else "unknown batch error"
                results[custom_id] = ProviderResult(
                    text="", tool_calls_made=0, input_tokens=0, output_tokens=0,
                    iterations=0, stop_reason="error", error=err_msg,
                )
            else:
                results[custom_id] = ProviderResult(
                    text="", tool_calls_made=0, input_tokens=0, output_tokens=0,
                    iterations=0, stop_reason="error",
                    error=f"batch entry unexpected type: {res.type}",
                )
        return results


def _normalize_batch_status(provider_status: str) -> str:
    """Map Anthropic processing_status to our normalized vocab."""
    if provider_status == "ended":
        return "ended"
    if provider_status in ("canceling", "canceled"):
        return "errored"
    return "in_progress"
