"""LLMProvider protocol — provider-neutral interface for the ReAct agent loop.

Every provider adapter implements this. Tasks call `provider.run_loop(...)` and
never see SDK-specific objects. Swapping providers is a one-line change in
`config.py` (the PROVIDER_NAME env var).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from tools.base import Tool


@dataclass
class ProviderResult:
    """Return shape from a provider's ReAct loop. Provider-neutral."""

    text: str                              # final assistant text (after end_turn)
    tool_calls_made: int                   # how many tool calls the model issued
    input_tokens: int                      # summed across all iterations
    output_tokens: int                     # summed across all iterations
    iterations: int                        # how many model→tool→model rounds
    stop_reason: str                       # normalized: "end_turn" | "tool_cap" | "max_iter" | "error"
    error: str | None = None               # populated only on stop_reason="error"


class LLMProvider(Protocol):
    """A model-agnostic ReAct loop runner.

    Implementations:
      - AnthropicProvider — wraps anthropic SDK
      - OpenAIProvider    — wraps openai SDK
      - GeminiProvider    — wraps google-genai SDK (stub for v0.1.0)
    """

    name: str                              # "anthropic" | "openai" | "gemini"
    model: str                             # e.g. "claude-sonnet-4-5"

    def run_loop(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[Tool],
        max_iterations: int = 10,
        tool_call_cap: int | None = None,
    ) -> ProviderResult:
        """Run a ReAct agent loop.

        - system_prompt: the persona/instructions block
        - user_message: the initial user turn
        - tools: list of Tool instances; the provider translates each .input_schema
                 to its own tool-use format and calls the matching tool by .name
        - max_iterations: hard cap on model→tool→model rounds
        - tool_call_cap: soft cap on total tool calls across the loop (None = no cap)

        Returns a ProviderResult with the final text and usage stats.
        """
        ...
