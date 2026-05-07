"""Module 13 — Industry pulse (2-3 category-level stories in last 60 days)."""

VERSION = "v1.1.0"

SYSTEM_PROMPT = """You are a B2B sales research agent. Identify 2-3 recent
category-level news stories (last 60 days) about the company's INDUSTRY (not
the company itself), by extracting facts from the research context provided
in the user message.

Output JSON:
```json
{
  "industry": "Vertical SaaS for restaurants",
  "stories": [
    {
      "headline": "Toast acquires xtraCHEF, signaling restaurant-tech consolidation",
      "url": "https://...",
      "buying_implication": "industry movement"
    },
    {
      "headline": "...",
      "url": "https://...",
      "buying_implication": null
    }
  ],
  "industry_movement_detected": true,
  "sources": ["https://..."],
  "confidence": "high"
}
```

Rules:
- Stories must be from the last 60 days. Older stories: skip.
- buying_implication is "industry movement" iff the story implies the *category*
  is consolidating, AI-disrupted, or going through a structural shift that
  affects buying behaviour. Otherwise null.
  Routine funding rounds for ONE player are NOT industry movement.
- industry_movement_detected is true iff at least one story has
  buying_implication="industry movement".
- 2-3 stories. If only 1 truly relevant story exists, include only it and set
  confidence="medium".
- The "industry" field captures the category as you see it (one phrase).
"""

JSON_SCHEMA = {
    "type": "object",
    "required": ["industry", "stories", "industry_movement_detected", "sources", "confidence"],
    "properties": {
        "industry": {"type": "string"},
        "stories": {
            "type": "array",
            "minItems": 1, "maxItems": 3,
            "items": {
                "type": "object",
                "required": ["headline", "url"],
                "properties": {
                    "headline": {"type": "string"},
                    "url": {"type": "string"},
                    "buying_implication": {"type": ["string", "null"],
                                           "enum": ["industry movement", None]},
                },
            },
        },
        "industry_movement_detected": {"type": "boolean"},
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
