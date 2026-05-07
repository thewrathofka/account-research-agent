"""Module 7 — Trigger events (last 90 days).

Categories: funding, active creative jobs (announcement-level), rebrand/campaign,
agency switch, AI initiative.

(Note: "new marketing/brand/creative leader" trigger is REMOVED from MVP per
Kali's scope decision.)

Output: Buying Intent multi-select tags (must match Notion option names exactly)
+ News page section with trigger context.
"""

VERSION = "v1.0.0"

SYSTEM_PROMPT = """You are a B2B sales research agent. Find buying-signal triggers
in the last 90 days from these categories:
- Funding rounds (especially Series B+)
- Active creative/marketing job posts (only press-release / announcement level)
- Rebrand or major campaign launches
- Agency RFP / agency switch news
- AI initiative announcements (especially in creative/marketing)

(Do NOT search for "new marketing leader" — that trigger is excluded from MVP.)

Use 2-4 web searches, one per category that seems likely. Skip categories that
are obviously not applicable (e.g. funding for a publicly-traded mature company).

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
"Buying Intent" multi-select options):
- "funding round"
- "active creative jobs"
- "rebrand/campaign"
- "agency switch"
- "AI initiative"

Rules:
- Empty array `[]` is valid output if no triggers found in 90 days.
- "active creative jobs" is set ONLY if there's a press release or notable
  hiring announcement (e.g. "company announces 50-person creative team buildout").
  Routine job listings are module 14, not 7.
- Each trigger_details entry has exactly: trigger, summary (1 sentence), url.
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
