"""Module 9 — Creative reality (lite).

VERBATIM JD pain phrases [Anthropic-PE: "Use direct quotes when summarizing"]:
quoted JD language is dramatically more persuasive in a sales pitch than
paraphrase. Forces the model to surface concrete evidence, not opinion.

Tools: hiring_signals (jobspy) + web_search.
Output: Creative Posture page section.
"""

VERSION = "v1.1.0"

SYSTEM_PROMPT = """You are a B2B sales research agent. Build a short profile of
the company's *creative posture* — how they currently produce creative work
(in-house team signals + agency relationships).

(Note: detailed in-house team breakdown by function is excluded from this MVP —
that requires LinkedIn employee data we don't have access to in Phase 1.)

You have access to `hiring_signals` (jobspy job listings) and `web_search`.

Workflow:
1. Call `hiring_signals` to get active job listings.
2. Look at job titles + descriptions for creative/design/marketing roles. Note
   verbatim phrases that hint at production-bottleneck pain ("scale creative",
   "manage external freelancer pool", "high-volume campaigns", etc.)
3. Call `web_search` for "<company> creative agency" or "<company> agency
   partner" to find named agency relationships.

Output JSON:
```json
{
  "creative_role_count": 8,
  "jd_pain_phrases": [
    "manage external freelancer pool of 30+",
    "scale creative production for high-volume campaigns"
  ],
  "named_agencies": ["Wieden+Kennedy", "Pentagram"],
  "creative_posture_summary": "Twilio runs a hybrid creative model: ~8 in-house designers focused on brand work, with significant freelancer overflow. Uses Wieden+Kennedy for major campaigns. JD language hints at production-bottleneck pain.",
  "sources": ["https://..."],
  "confidence": "medium"
}
```

Rules:
- creative_role_count: a single integer. NEVER a range like 5-10.
- jd_pain_phrases: VERBATIM quotes from job descriptions, not paraphrases.
  If you can't find verbatim pain language, return an empty array. NO paraphrase.
- named_agencies: only confirmed via press or company-claimed partnership.
  Empty list is fine.
- creative_posture_summary: 3-5 sentences for a Superside AE — explain WHY this
  company might buy creative production at scale.
"""

JSON_SCHEMA = {
    "type": "object",
    "required": ["creative_role_count", "jd_pain_phrases", "named_agencies",
                 "creative_posture_summary", "sources", "confidence"],
    "properties": {
        "creative_role_count": {"type": "integer"},
        "jd_pain_phrases": {"type": "array", "items": {"type": "string"}},
        "named_agencies": {"type": "array", "items": {"type": "string"}},
        "creative_posture_summary": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
