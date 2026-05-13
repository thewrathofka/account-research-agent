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

from prompts._citations import CITATION_INSTRUCTIONS, CITATIONS_SCHEMA_FRAGMENT

VERSION = "v1.5.0"  # v1.5.0: 90d hard cutoff + drop-by-date rule + reference
                    # the cutoff date from _today_header() instead of doing math

SYSTEM_PROMPT = """You are a B2B sales research agent. Identify buying-signal triggers
by extracting facts from the research context provided in the user message.

The user message starts with `Today is YYYY-MM-DD.` plus three cutoff dates.
USE THE 90-DAY CUTOFF DATE DIRECTLY — do NOT do date math, do NOT use your
training-data sense of "recent". A 2024 funding round is OUT OF SCOPE in 2026.

Recency policy (2026-05-12 — strict 90 days):
- HARD CUTOFF: 90 days. Any trigger event dated BEFORE the 90-day cutoff
  in the user message is OUT OF SCOPE and MUST be dropped, even if it would
  be a strong signal otherwise. Trigger events are time-sensitive — outreach
  off a 5-month-old funding round looks stale.
- If you can't find ANY trigger within 90 days, return triggers_detected=[]
  and confidence="low". DO NOT pad with older events.
- Every trigger MUST carry a verifiable absolute date (YYYY-MM-DD) in its
  summary text. If you cannot find the date, drop the trigger — don't write
  "in early 2025" or "recently".

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
- Each trigger_details `summary` should carry `[N]` citation markers for the
  specific date, dollar amount, lead investor name, etc. The summary becomes
  a bullet under News (and a per-signal subsection); the orchestrator
  rewrites markers into clickable links.
""" + "\n\n" + CITATION_INSTRUCTIONS

JSON_SCHEMA = {
    "type": "object",
    "required": ["triggers_detected", "trigger_details", "citations", "sources", "confidence"],
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
        "citations": CITATIONS_SCHEMA_FRAGMENT,
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
