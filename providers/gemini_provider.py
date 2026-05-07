"""Gemini provider — stub for v0.1.0. Implementation lives in a future PR.

The class is wired up so `PROVIDER_NAME=gemini` fails with a clear message
instead of an AttributeError, and the abstraction's swap surface is exercised
when someone goes to fill this in.
"""

from __future__ import annotations

import config
from providers.base import LLMProvider, ProviderResult
from tools.base import Tool


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, model: str | None = None):
        self.model = model or config.GEMINI_MODEL

    def run_loop(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[Tool],
        max_iterations: int = 10,
        tool_call_cap: int | None = None,
    ) -> ProviderResult:
        raise NotImplementedError(
            "GeminiProvider is a stub. To implement: install `google-genai`, "
            "translate the run_loop pattern from anthropic_provider.py / openai_provider.py "
            "to Gemini's `functionCall` / `functionResponse` format. See providers/base.py "
            "for the contract."
        )
