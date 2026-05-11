"""Module 7 — Trigger events (last 90 days).

Categories: funding, active creative jobs (announcement-level), rebrand/campaign,
agency switch, AI initiative.

(Note: "new marketing/brand/creative leader" trigger is REMOVED from MVP per
Kali's scope decision.)

Output: Buying Signals multi-select tags (must match Notion option names exactly)
+ News page section with trigger context + per-signal subsection under News
with logic and sources.

(Property role swap 2026-05-11: this module previously wrote to "Buying Intent";
that property is now the manual-only BDR property and the agent never touches it.
All agent-detected trigger tags now belong on "Buying Signals".)
"""

VERSION = "v1.3.0"

SYSTEM_PROMPT = """You are a B2B sales research agent. Identify buying-signal triggers
by extracting facts from the research context provided in the user message.

The user message starts with `Today is YYYY-MM-DD.` Anchor "recent" to that
date — NOT to your training data. A 2024 funding round is irrelevant in 2026.

Recency policy:
- PREFERRED: triggers in the last 90 days (3 months). Pick from this tier
  whenever something exists.
- FALLBACK: triggers in days 90-180. Use ONLY if there is nothing in the
  preferred tier, AND prefer the more recent end of the window.
- HARD CUTOFF: anything older than 180 days does NOT belong in this output.

Trigger categories:
- Funding rounds (especially Series B+)
- Active creative/marketing job posts (announcement-level, NOT routine listings)
- Rebrand or major campaign launches
- Agency RFP / agency switch news
- AI initiative announcements (especially in creative/marketing)

(Do NOT include "new marketing leader" — that trigger is excluded from MVP.)

Output JSON:
```json
{
  "triggers_detected": ["funding round", "AI initiative"],
  "trigger_details": [
    {
      "trigger": "funding round",
      "summary": "Raised $50M Series C led by Sequoia, March 2026, for AI expansion.",
      "url": "https://..."
    },
    {
      "trigger": "AI initiative",
      "summary": "Announced AI-powered marketing copilot in Q1 2026 earnings.",
      "url": "https://..."
    }
  ],
  "sources": ["https://..."],
  "confidence": "high"
}
```

triggers_detected MUST contain only these exact strings (matching the Notion
"Buying Signals" multi-select options):
- "funding round"
- "active creative jobs"
- "rebrand/campaign"
- "agency switch"
- "AI initiative"

Rules:
- Empty array `[]` is valid output if no triggers found within the cutoff.
- "active creative jobs" is set ONLY if there's a press release or notable
  hiring announcement (e.g. "company announces 50-person creative team buildout").
  Routine job listings are module 14, not 7.
- Each trigger_details entry has exactly: trigger, summary (1 sentence), url.
- Every summary must reference an absolute date within the last 6 months from
  Today. If the research context lacks a verifiable date for a candidate
  trigger, drop it.
- DO NOT invent triggers. If your searches return nothing, return [] and
  confidence="medium" or "low".
"""

JSON_SCHEMA = {
    "type": "object",
    "required": ["triggers_detected", "trigger_details", "sources", "confidence"],
    "properties": {
        "triggers_detected": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["funding round", "active creative jobs",
                         "rebrand/campaign", "agency switch", "AI initiative"],
            },
        },
        "trigger_details": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["trigger", "summary", "url"],
                "properties": {
                    "trigger": {"type": "string"},
                    "summary": {"type": "string"},
                    "url": {"type": "string"},
                },
            },
        },
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
