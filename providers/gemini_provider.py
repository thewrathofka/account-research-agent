"""Gemini provider — real run_loop via google-genai.

Implements the same LLMProvider contract as the Anthropic and OpenAI adapters
so that `--provider gemini` Just Works for tool-using tasks. Batch mode is
explicitly NOT implemented; the CLI surfaces this as a clear error rather than
silently falling back to sync, per the Fix Appendix acceptance criteria.

Caching: Gemini has explicit context caching, but we don't wire it here — the
short prompts in this project don't benefit enough to justify the complexity.
The flag stays False so the orchestrator's cache_system_prompt is a no-op.
"""

from __future__ import annotations

import os
from typing import Any

import config
from providers.base import BatchHandle, BatchRequest, LLMProvider, ProviderResult
from tools.base import Tool


class GeminiProvider(LLMProvider):
    name = "gemini"
    models = config.GEMINI_MODELS
    supports_caching = False
    supports_batch = False  # Set True only after Gemini batch API is implemented.

    def __init__(self):
        self.model = self.models["smart"]
        # Lazy import so the SDK is only required when this provider is selected.
        try:
            from google import genai
            from google.genai import types  # noqa: F401  (re-exported for run_loop)
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "google-genai is not installed. Run "
                "`pip install google-genai` to use the Gemini provider."
            ) from e
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Add it to .env to use --provider gemini."
            )
        self._genai = genai
        self.client = genai.Client(api_key=api_key)

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
        from google.genai import types

        model = self._resolve_model(model_tier)
        tool_by_name: dict[str, Tool] = {t.name: t for t in tools}

        function_decls = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters=t.input_schema,
            )
            for t in tools
        ]
        gemini_tools = (
            [types.Tool(function_declarations=function_decls)] if function_decls else None
        )

        contents: list[Any] = [
            types.Content(role="user", parts=[types.Part(text=user_message)]),
        ]

        input_tokens = 0
        output_tokens = 0
        tool_calls_made = 0

        try:
            for iteration in range(1, max_iterations + 1):
                response = self.client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        tools=gemini_tools,
                        max_output_tokens=config.MAX_TOKENS,
                    ),
                )
                usage = getattr(response, "usage_metadata", None)
                if usage is not None:
                    input_tokens += getattr(usage, "prompt_token_count", 0) or 0
                    output_tokens += getattr(usage, "candidates_token_count", 0) or 0

                function_calls = getattr(response, "function_calls", None) or []

                if not function_calls:
                    return ProviderResult(
                        text=(getattr(response, "text", "") or ""),
                        tool_calls_made=tool_calls_made,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        iterations=iteration,
                        stop_reason="end_turn",
                        model_used=model,
                    )

                if tool_call_cap is not None and tool_calls_made >= tool_call_cap:
                    return ProviderResult(
                        text="", tool_calls_made=tool_calls_made,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        iterations=iteration, stop_reason="tool_cap",
                        error=f"hit tool_call_cap ({tool_call_cap})",
                        model_used=model,
                    )

                # Echo the model's tool-call turn back in the conversation, then
                # append a tool turn with one Part per function_response.
                contents.append(response.candidates[0].content)
                tool_parts: list[Any] = []
                for call in function_calls:
                    tool_calls_made += 1
                    tool = tool_by_name.get(call.name)
                    args = dict(call.args) if call.args else {}
                    result = (
                        tool(**args) if tool else f"Unknown tool: {call.name}"
                    )
                    tool_parts.append(types.Part.from_function_response(
                        name=call.name, response={"result": result},
                    ))
                contents.append(types.Content(role="tool", parts=tool_parts))

            return ProviderResult(
                text="", tool_calls_made=tool_calls_made,
                input_tokens=input_tokens, output_tokens=output_tokens,
                iterations=max_iterations, stop_reason="max_iter",
                error=f"hit max_iterations ({max_iterations})",
                model_used=model,
            )
        except Exception as e:
            return ProviderResult(
                text="", tool_calls_made=tool_calls_made,
                input_tokens=input_tokens, output_tokens=output_tokens,
                iterations=0, stop_reason="error",
                error=f"{type(e).__name__}: {e}",
                model_used=model,
            )

    # ---- Batch API ----

    def submit_batch(self, requests: list[BatchRequest]) -> BatchHandle:
        raise NotImplementedError(
            "GeminiProvider does not yet support batch mode. "
            "Use --provider anthropic or --provider openai for batch runs, "
            "or run --provider gemini WITHOUT --batch."
        )

    def poll_batch(self, handle: BatchHandle) -> str:
        raise NotImplementedError("GeminiProvider batch is not implemented")

    def fetch_batch_results(self, handle: BatchHandle) -> dict[str, ProviderResult]:
        raise NotImplementedError("GeminiProvider batch is not implemented")
