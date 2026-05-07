"""Provider registry. `get_provider()` returns the active provider per config.PROVIDER_NAME."""

from __future__ import annotations

import config
from providers.anthropic_provider import AnthropicProvider
from providers.base import LLMProvider, ProviderResult
from providers.gemini_provider import GeminiProvider
from providers.openai_provider import OpenAIProvider


_REGISTRY: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}


def get_provider(name: str | None = None) -> LLMProvider:
    """Return an instance of the named provider (or config.PROVIDER_NAME if None)."""
    name = name or config.PROVIDER_NAME
    if name not in _REGISTRY:
        raise ValueError(
            f"Unknown provider: {name!r}. "
            f"Known: {sorted(_REGISTRY)}. Set PROVIDER env var to one of these."
        )
    return _REGISTRY[name]()


__all__ = ["LLMProvider", "ProviderResult", "get_provider"]
