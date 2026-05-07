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
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

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
    for name, value in always_required.items():
        if not value or "PLACEHOLDER" in value:
            print(f"ERROR: {name} is missing or still a placeholder in .env")
            sys.exit(1)


# ---- Active provider — change THIS LINE to swap models ----
# Valid values: "anthropic" | "openai" | "gemini" (gemini stub-only as of v0.1.0)
PROVIDER_NAME = os.getenv("PROVIDER", "anthropic")

# ---- Models per provider ----
ANTHROPIC_MODEL = "claude-sonnet-4-5"
OPENAI_MODEL = "gpt-4.1"
GEMINI_MODEL = "gemini-2.5-pro"

# ---- Generation params ----
MAX_TOKENS = 4096
MAX_AGENT_ITERATIONS = 10

# ---- Concurrency + cost gates ----
DEFAULT_CONCURRENCY = 5
TAVILY_SEARCHES_PER_TASK_CAP = 8

_validate_keys()
