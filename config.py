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
# Apify is optional — module 10 (ad library) degrades gracefully to "not
# configured" output when absent so the rest of the pipeline keeps working.
APIFY_API_KEY = os.getenv("APIFY_API_KEY")

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
    "smart": "claude-sonnet-4-6",
    "fast": "claude-haiku-4-5",
}
OPENAI_MODELS = {
    "smart": "gpt-5.5",
    "fast": "gpt-5.5-mini",
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
# 8192 covers the ResearchPass module's broad research output (~4-5k tokens of
# raw_research). Synthesis tasks output 200-800 tokens — the cap is irrelevant
# for them, no cost impact.
MAX_TOKENS = 8192
MAX_AGENT_ITERATIONS = 10

# ---- Concurrency + cost gates ----
DEFAULT_CONCURRENCY = 5
TAVILY_SEARCHES_PER_TASK_CAP = 8
# Module 10 calls one platform per scrape; cap = 3 covers LinkedIn (always),
# Meta (gated), TikTok (gated). MAX_APIFY_RESULTS_PER_PLATFORM bounds per-call
# cost — Meta scraper is ~$0.65/1k results so 50 caps that at ~$0.03/scrape.
APIFY_CALLS_PER_TASK_CAP = 3
MAX_APIFY_RESULTS_PER_PLATFORM = 50

# Module 02 persona-headcount gate. One-time per account. Apply title + geo
# filters in the tool (no LLM judgment per profile). Actor =
# automation-lab/linkedin-company-employees-scraper in cookie-free SERP mode,
# which caps at ~100 employees regardless of this value — so requesting more
# is wasted money. ~$0.58/account on the FREE Apify tier ($0.005 start +
# 100 × $0.00575 per employee); drops to ~$0.14-$0.30/account on paid tiers.
IN_SCOPE_HEADCOUNT_MAX_RESULTS = 100
# Account fails the persona gate (-> out_of_scope) when in-scope total is below
# this threshold. "In-scope" = title matches marketing/creative/brand keywords
# AND location matches NA/EU/UK/Norway/Switzerland.
PERSONA_GATE_MIN_HEADCOUNT = 5

# Per-task and per-account cost budgets in USD (Fix Appendix #19).
# Numbers reflect Sonnet-4.5 + Haiku-4.5 pricing observed in v0.3.0 baseline.
# Eval runner fails if a measured task cost exceeds budget by >20%.
MAX_COST: dict[str, float] = {
    "module_01_gate": 0.04,
    "research_pass": 0.06,
    "module_03_revenue_model": 0.02,
    "module_05_corporate_structure": 0.02,
    "module_06_structural_news": 0.02,
    "module_07_trigger_events": 0.03,
    "module_09_creative_reality": 0.05,
    "module_12_competitor_snapshot": 0.02,
    "module_13_industry_pulse": 0.02,
    "module_14_hiring_signal": 0.05,
    "module_10_ad_library": 0.06,  # Phase 2 — token cost; Apify tool cost separate
    "module_04_pain_points": 0.04,  # Phase 2b — synthesis-only, single LLM call
    "company_overview": 0.04,
    "_account_total": 0.40,  # ceiling per account across all tasks (bumped for modules 4 + 10)
}
COST_REGRESSION_OVERAGE = 0.20  # 20% headroom before failing the eval

# Per-1M-token rates by tier-resolved model name. Used by evals to translate the
# token counters from the run log into a USD cost estimate.
#
# cached_input semantics differ across providers — these prices are NOT directly
# comparable across the three vendor blocks below:
#   - Anthropic: explicit `cache_control` blocks the agent sets; we get exact
#     hit/miss accounting via `cache_read_input_tokens`. Cost is real.
#   - OpenAI:   AUTOMATIC cache for prompts >=1024 tokens; no developer control.
#     `prompt_tokens_details.cached_tokens` reports auto-cache hits. The reported
#     rate is accurate per-token but the HIT RATE will look different from
#     Anthropic's, so the "cached_input" line in the cost block is provider-
#     conditional. Don't compare ratios across providers without context.
#   - Gemini:   no equivalent token field today; cached_input always reads 0.
MODEL_PRICING_PER_M_TOKENS: dict[str, dict[str, float]] = {
    # Anthropic — Sonnet 4.6 (current smart tier) + Haiku 4.5 (fast tier).
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cached_input": 0.30},
    "claude-haiku-4-5":  {"input": 1.00, "output": 5.00,  "cached_input": 0.10},
    # OpenAI — gpt-5.5 (smart) + gpt-5.5-mini (fast). Update once final pricing
    # is confirmed; current numbers carry forward gpt-4.1's per-M cost as a
    # PLACEHOLDER so cost-regression evals don't no-op against unknown models.
    # ⚠ Forecasts at scale on --provider openai are not trustworthy until these
    # numbers are re-pinned against the real published rates.
    "gpt-5.5":           {"input": 2.00, "output": 8.00,  "cached_input": 0.50},
    "gpt-5.5-mini":      {"input": 0.40, "output": 1.60,  "cached_input": 0.10},
    # Google — Gemini 2.5
    "gemini-2.5-pro":    {"input": 3.50, "output": 10.50, "cached_input": 0.875},
    "gemini-2.5-flash":  {"input": 0.30, "output": 2.50,  "cached_input": 0.075},
}


def estimate_usd_cost(
    input_tokens: int,
    output_tokens: int,
    model_used: str | None,
    cached_input_tokens: int = 0,
) -> float:
    """Return per-call USD cost using MODEL_PRICING_PER_M_TOKENS, or 0.0 if unknown.

    `cached_input_tokens` are billed at the cached rate; the rest of input_tokens
    at the full input rate. When model_used is missing or unmapped we return 0.0
    rather than guessing — the eval will silently treat this case as within
    budget, which is fine because the budget gate only fires when we KNOW we
    overshot.
    """
    if not model_used:
        return 0.0
    pricing = MODEL_PRICING_PER_M_TOKENS.get(model_used)
    if pricing is None:
        return 0.0
    fresh_input = max(0, input_tokens - cached_input_tokens)
    return (
        fresh_input * pricing["input"] / 1_000_000
        + cached_input_tokens * pricing.get("cached_input", pricing["input"]) / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
    )


# ---- Tool-side cost rates (USD per call) ----
# Tavily: production tier is $0.008/search; the 1K/month free quota effectively
# zeroes out small batches but we treat all searches at the paid rate for the
# end-of-run cost printout (under-reporting hides real cost when scaling up).
TAVILY_COST_PER_SEARCH = 0.008

# Apify: cost is per-actor and per-result, accumulated on the Apify dashboard.
# We do NOT estimate Apify cost from the run log — `tool_results_seen` doesn't
# distinguish "free LinkedIn page hit" from "paid Meta ad library page hit".
# The CLI cost block flags Apify spend as "see Apify dashboard" instead.


_validate_keys()
