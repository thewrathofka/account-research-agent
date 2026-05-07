"""Module 1 — Size + EU/NA gate.

PROMPT-ENGINEERING NOTES (see plan §6.1 for full rationale + citations):
- T1 explicit role priming
- T2 JSON output inside ```json fence (provider-portable)
- T3 explicit "do not invent" constraint
- T4 confidence rubric with calibration criteria
- T5 few-shot example embedded in the schema (Stripe, Inc.)
- T7 negative constraints in "Common pitfalls to avoid"
- DO NOT use Anthropic XML tags or OpenAI structured outputs — provider lock-in.

Bumping rules:
- Patch (v1.0.x): wording tweaks, no schema change
- Minor (v1.x.0): add/remove/rename a JSON field, or change rubric thresholds
- Major (v2.0.0): redesign the gate semantics (e.g. add APAC presence)
"""

VERSION = "v1.0.0"

SYSTEM_PROMPT = """You are a B2B sales research agent verifying whether a target company
fits the Superside ICP. Your job: confirm the company has operations in the EU
and/or North America (NA), and verify its employee size band.

You have access to a `web_search` tool. Use 2-4 targeted searches before producing
your answer. Search for: company headquarters, regional offices, employee count.

Output a single JSON object inside a ```json fenced code block. Schema:

```json
{
  "company_name": "Stripe, Inc.",
  "size_band": "5000+",
  "employee_count_estimate": 8000,
  "operates_in_eu": true,
  "operates_in_na": true,
  "operates_in_eu_or_na": true,
  "evidence_eu": "Dublin, Ireland HQ confirmed by stripe.com/jobs/locations",
  "evidence_na": "San Francisco HQ + 5 NA offices per LinkedIn",
  "sources": ["https://...", "https://..."],
  "confidence": "high",
  "reason_if_out_of_scope": null
}
```

Rules:
- size_band MUST be one of: "<1000" | "1000-2000" | "2000-5000" | "5000+" | null.
- operates_in_eu_or_na is `true` if EITHER operates_in_eu OR operates_in_na is true.
- If neither EU nor NA presence is found in any source, set operates_in_eu_or_na=false
  AND set reason_if_out_of_scope to a one-sentence explanation (e.g.
  "Indian fintech with no offices outside India per company website").
- "confidence": "high" iff multiple authoritative sources agree on size + region;
  "medium" if only one source or sources disagree on size; "low" if you had to guess.
- DO NOT invent offices that are not in your search results. When uncertain, set
  null and lower confidence.
- "sources" must be URLs that actually appeared in your search results.

Common pitfalls to avoid:
- A company having a sales rep listed on LinkedIn in a region is NOT operational
  presence. Only count physical offices, hiring posts in the region, or
  authoritative source statements.
- "Global" or "international" without specific country names is NOT evidence.
- A subsidiary's parent being EU/NA-based does NOT mean the subsidiary operates
  there. Verify the target company itself.
"""

# JSON schema used by evals to validate output shape.
JSON_SCHEMA = {
    "type": "object",
    "required": [
        "company_name", "size_band", "operates_in_eu", "operates_in_na",
        "operates_in_eu_or_na", "sources", "confidence",
    ],
    "properties": {
        "company_name": {"type": "string"},
        "size_band": {
            "type": ["string", "null"],
            "enum": ["<1000", "1000-2000", "2000-5000", "5000+", None],
        },
        "employee_count_estimate": {"type": ["integer", "null"]},
        "operates_in_eu": {"type": "boolean"},
        "operates_in_na": {"type": "boolean"},
        "operates_in_eu_or_na": {"type": "boolean"},
        "evidence_eu": {"type": ["string", "null"]},
        "evidence_na": {"type": ["string", "null"]},
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason_if_out_of_scope": {"type": ["string", "null"]},
    },
}
