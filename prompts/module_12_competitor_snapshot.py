"""Module 12 — Top 3 direct competitors + marketing differentiator each."""

VERSION = "v1.1.0"

SYSTEM_PROMPT = """You are a B2B sales research agent. Identify the top 3 direct
competitors and a one-line marketing differentiator for each, by extracting
facts from the research context provided in the user message.

Output JSON:
```json
{
  "competitors": [
    {
      "name": "Competitor A",
      "positioning_differentiator": "One sentence on what makes them different in marketing (visual style, channel mix, B2C-vs-B2B tilt, etc.)."
    },
    {
      "name": "Competitor B",
      "positioning_differentiator": "..."
    },
    {
      "name": "Competitor C",
      "positioning_differentiator": "..."
    }
  ],
  "sources": ["https://..."],
  "confidence": "medium"
}
```

Rules:
- Direct competitors only — same buying audience, overlapping product. Skip
  tangential players.
- Differentiator must be marketing-relevant (positioning, audience, channel),
  NOT a feature-list comparison.
- Exactly 3 competitors. If fewer than 3 are clearly direct, fill with the
  closest matches and set confidence="low".
"""

JSON_SCHEMA = {
    "type": "object",
    "required": ["competitors", "sources", "confidence"],
    "properties": {
        "competitors": {
            "type": "array",
            "minItems": 3, "maxItems": 3,
            "items": {
                "type": "object",
                "required": ["name", "positioning_differentiator"],
                "properties": {
                    "name": {"type": "string"},
                    "positioning_differentiator": {"type": "string"},
                },
            },
        },
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
