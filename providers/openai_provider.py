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
from providers.base import BatchHandle, BatchRequest, LLMProvider, ProviderResult
from rate_limit import OPENAI_LIMITER
from tools.base import Tool


class OpenAIProvider(LLMProvider):
    name = "openai"
    models = config.OPENAI_MODELS
    supports_caching = True  # auto-caches above 1024 tokens — no hint needed
    supports_batch = True    # Fix Appendix #22 — file-upload Batch API implemented below

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
                with OPENAI_LIMITER:
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


    # ---- Batch API (Fix Appendix #22 — file-upload flow) ----

    def submit_batch(self, requests: list[BatchRequest]) -> BatchHandle:
        """Submit a batch via OpenAI's Batch API.

        Flow: build JSONL with one /v1/chat/completions request per BatchRequest,
        upload the file with purpose='batch', then create a batch pointed at it.
        Each line carries the BatchRequest's custom_id so we can pair results.
        """
        import json as _json
        from datetime import datetime as _dt, timezone as _tz

        lines: list[bytes] = []
        for r in requests:
            body = {
                "model": self._resolve_model(r.model_tier),
                "messages": [
                    {"role": "system", "content": r.system_prompt},
                    {"role": "user", "content": r.user_message},
                ],
                "max_tokens": r.max_tokens or config.MAX_TOKENS,
            }
            lines.append(_json.dumps({
                "custom_id": r.custom_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": body,
            }).encode("utf-8"))
        payload = b"\n".join(lines)

        with OPENAI_LIMITER:
            file_obj = self.client.files.create(
                file=("batch.jsonl", payload),
                purpose="batch",
            )
        with OPENAI_LIMITER:
            batch = self.client.batches.create(
                input_file_id=file_obj.id,
                endpoint="/v1/chat/completions",
                completion_window="24h",
            )
        return BatchHandle(
            batch_id=batch.id,
            provider_name=self.name,
            submitted_at=_dt.now(_tz.utc).isoformat(),
            request_count=len(requests),
            expected_status=_normalize_openai_batch_status(batch.status),
        )

    def poll_batch(self, handle: BatchHandle) -> str:
        with OPENAI_LIMITER:
            batch = self.client.batches.retrieve(handle.batch_id)
        return _normalize_openai_batch_status(batch.status)

    def fetch_batch_results(self, handle: BatchHandle) -> dict[str, ProviderResult]:
        """Read the output JSONL file and parse rows into ProviderResults."""
        import json as _json

        with OPENAI_LIMITER:
            batch = self.client.batches.retrieve(handle.batch_id)
        if not getattr(batch, "output_file_id", None):
            raise RuntimeError(
                f"OpenAI batch {handle.batch_id} has no output_file_id "
                f"(status={batch.status}). Cannot fetch results."
            )
        with OPENAI_LIMITER:
            content = self.client.files.content(batch.output_file_id).read().decode("utf-8")

        out: dict[str, ProviderResult] = {}
        for line in content.splitlines():
            if not line.strip():
                continue
            row = _json.loads(line)
            custom_id = row.get("custom_id")
            err = row.get("error")
            if err:
                out[custom_id] = ProviderResult(
                    text="", tool_calls_made=0, input_tokens=0, output_tokens=0,
                    iterations=0, stop_reason="error",
                    error=err.get("message") if isinstance(err, dict) else str(err),
                )
                continue
            response = row.get("response") or {}
            body = response.get("body") or {}
            choices = body.get("choices") or []
            msg = choices[0].get("message") if choices else {}
            usage = body.get("usage") or {}
            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
            out[custom_id] = ProviderResult(
                text=(msg or {}).get("content", "") or "",
                tool_calls_made=0,
                input_tokens=usage.get("prompt_tokens", 0) or 0,
                output_tokens=usage.get("completion_tokens", 0) or 0,
                cached_input_tokens=cached,
                iterations=1,
                stop_reason="end_turn",
                model_used=body.get("model"),
            )
        return out


def _normalize_openai_batch_status(provider_status: str) -> str:
    """Map OpenAI Batch lifecycle to our normalized vocab."""
    if provider_status == "completed":
        return "ended"
    if provider_status in ("failed", "expired", "cancelling", "cancelled"):
        return "errored"
    # validating | in_progress | finalizing
    return "in_progress"


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
