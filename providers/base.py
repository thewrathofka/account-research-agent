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
    cached_input_tokens: int = 0           # subset of input_tokens read from cache
    iterations: int = 0                    # how many model→tool→model rounds
    stop_reason: str = "end_turn"          # normalized: "end_turn" | "tool_cap" | "max_iter" | "error"
    error: str | None = None               # populated only on stop_reason="error"
    model_used: str | None = None          # the actual provider-specific model name used


class LLMProvider(Protocol):
    """A model-agnostic ReAct loop runner.

    Implementations:
      - AnthropicProvider — wraps anthropic SDK
      - OpenAIProvider    — wraps openai SDK
      - GeminiProvider    — wraps google-genai SDK (stub for v0.1.0)

    Capability flags exposed to callers:
      - models: dict mapping tier ("smart" / "fast") → provider-specific model name
      - supports_caching: True if provider has explicit prompt caching support
    """

    name: str                              # "anthropic" | "openai" | "gemini"
    model: str                             # default model — same as models["smart"]
    models: dict                           # tier → model name
    supports_caching: bool                 # True if .prepare_cache() actually does something

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
        """Run a ReAct agent loop.

        - system_prompt: the persona/instructions block
        - user_message: the initial user turn
        - tools: list of Tool instances; the provider translates each .input_schema
                 to its own tool-use format and calls the matching tool by .name
        - max_iterations: hard cap on model→tool→model rounds
        - tool_call_cap: soft cap on total tool calls across the loop (None = no cap)
        - model_tier: "smart" (default) | "fast" — picks the cheaper model for
                      lookup-heavy tasks. Provider maps the tier to its model name.
        - cache_system_prompt: if True AND supports_caching, mark the system
                      prompt for cache reuse. No-op for providers without explicit
                      caching (OpenAI auto-caches above 1024 tokens regardless).

        Returns a ProviderResult with the final text and usage stats.
        """
        ...
