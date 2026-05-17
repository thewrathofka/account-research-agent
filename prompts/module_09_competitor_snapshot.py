"""Module 12 — Top 3 direct competitors + marketing differentiator each."""

from prompts._citations import CITATION_INSTRUCTIONS, CITATIONS_SCHEMA_FRAGMENT

VERSION = "v1.3.0"  # v1.3.0: loosen "Exactly 3" → "1-3, no padding" (was forcing model to invent)
                    #         + per-tier confidence rubric

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
- 1-3 competitors. PREFER truly direct competitors over a forced count.
  If only 2 truly direct competitors exist, return 2 and set
  confidence="medium". If only 1 exists (rare — usually means the company
  is in a hyper-niche category), return 1 and set confidence="low". DO NOT
  pad with adjacent or tangential players just to hit a count of 3 — a
  fabricated competitor is worse than an honest gap for the BDR.

`confidence`:
- "high"   = 3 competitors named, each with a sourced differentiator citing
             the competitor's own marketing or named industry coverage.
- "medium" = 3 competitors named but some differentiators are inferred /
             generic; OR 2 truly direct competitors returned (and you
             refused to pad).
- "low"    = 1 truly direct competitor (very niche category); OR named 3
             but had to reach to adjacent players for the 3rd because the
             category is small.
""" + "\n\n" + CITATION_INSTRUCTIONS

JSON_SCHEMA = {
    "type": "object",
    "required": ["competitors", "citations", "sources", "confidence"],
    "properties": {
        "competitors": {
            "type": "array",
            "minItems": 1, "maxItems": 3,
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
