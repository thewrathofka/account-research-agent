"""Module 12 — Top 3 direct competitors + marketing differentiator each."""

from prompts._citations import CITATION_INSTRUCTIONS, CITATIONS_SCHEMA_FRAGMENT

VERSION = "v1.2.0"  # v1.2.0: citations array + [N] markers in positioning_differentiator

SYSTEM_PROMPT = """You are a B2B sales research agent. Identify the top 3 direct
competitors and a one-line marketing differentiator for each, by extracting
facts from the research context provided in the user message.

Output JSON:
```json
{
  "competitors": [
    {
      "name": "Competitor A",
      "positioning_differentiator": "One sentence on what makes them different in marketing (visual style, channel mix, B2C-vs-B2B tilt, etc.) [1]."
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
  "citations": [
    {"n": 1, "title": "competitor-a.com — about", "url": "https://..."}
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
- Each `positioning_differentiator` should carry one or two `[N]` citation
  markers tying the claim to a specific source. The differentiator becomes
  a bullet under Competitor Landscape; the orchestrator turns the markers
  into clickable links.
- Exactly 3 competitors. If fewer than 3 are clearly direct, fill with the
  closest matches and set confidence="low".
""" + "\n\n" + CITATION_INSTRUCTIONS

JSON_SCHEMA = {
    "type": "object",
    "required": ["competitors", "citations", "sources", "confidence"],
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
        "citations": CITATIONS_SCHEMA_FRAGMENT,
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
