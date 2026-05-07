"""Central config — env vars + tunable constants.

All Phase 1 modules import from here. To swap LLM providers, change PROVIDER below.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()

# ---- API keys ----
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")          # required iff PROVIDER is OpenAI
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY")            # required iff SEARCH_BACKEND in ("brave", "brave_jina")
JINA_API_KEY = os.getenv("JINA_API_KEY", "")          # optional — Jina Reader works without a key on free tier
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

# Search backend: "tavily" (default, current) | "brave" | "brave_jina"
SEARCH_BACKEND = os.getenv("SEARCH_BACKEND", "tavily")

# ---- Required keys depend on which provider is active ----
def _validate_keys() -> None:
    """Validate that the keys for the active provider are present."""
    always_required = {
        "TAVILY_API_KEY": TAVILY_API_KEY,
        "NOTION_API_KEY": NOTION_API_KEY,
        "NOTION_DATABASE_ID": NOTION_DATABASE_ID,
    }
    if PROVIDER_NAME == "anthropic":
        always_required["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY
    elif PROVIDER_NAME == "openai":
        always_required["OPENAI_API_KEY"] = OPENAI_API_KEY
    if SEARCH_BACKEND == "tavily":
        # already in always_required above
        pass
    elif SEARCH_BACKEND in ("brave", "brave_jina"):
        always_required["BRAVE_API_KEY"] = BRAVE_API_KEY
        # Tavily key not required when using Brave
        always_required.pop("TAVILY_API_KEY", None)
    for name, value in always_required.items():
        if not value or "PLACEHOLDER" in value:
            print(f"ERROR: {name} is missing or still a placeholder in .env")
            sys.exit(1)


# ---- Active provider — change THIS LINE to swap models ----
# Valid values: "anthropic" | "openai" | "gemini" (gemini stub-only as of v0.1.0)
PROVIDER_NAME = os.getenv("PROVIDER", "anthropic")

# ---- Models per provider, by tier ----
# Each task can declare a `model_tier` ("smart" default, "fast" for cheap lookups).
# Provider adapters map tier → provider-specific model. Falls back to "smart" if a
# tier is unknown.
ANTHROPIC_MODELS = {
    "smart": "claude-sonnet-4-5",
    "fast": "claude-haiku-4-5",
}
OPENAI_MODELS = {
    "smart": "gpt-4.1",
    "fast": "gpt-4.1-mini",
}
GEMINI_MODELS = {
    "smart": "gemini-2.5-pro",
    "fast": "gemini-2.5-flash",
}

# Backwards-compat single-model constants (used by some legacy log paths)
ANTHROPIC_MODEL = ANTHROPIC_MODELS["smart"]
OPENAI_MODEL = OPENAI_MODELS["smart"]
GEMINI_MODEL = GEMINI_MODELS["smart"]

# ---- Generation params ----
MAX_TOKENS = 4096
MAX_AGENT_ITERATIONS = 10

# ---- Concurrency + cost gates ----
DEFAULT_CONCURRENCY = 5
TAVILY_SEARCHES_PER_TASK_CAP = 8

_validate_keys()
