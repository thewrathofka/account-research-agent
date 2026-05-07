
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

VERSION = "v1.1.0"

SYSTEM_PROMPT = """You are a B2B sales research agent verifying ICP fit. Confirm the
company has operations in the EU and/or North America (NA), and verify its
employee size band.

Use the `web_search` tool 2-4 times. Search for: HQ + offices, employee count.

Output one JSON object in a ```json fenced block:

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
- size_band: one of "<1000" | "1000-2000" | "2000-5000" | "5000+" | null.
- employee_count_estimate: a single integer or null. NEVER a range like 1000-5000.
- operates_in_eu_or_na: true iff EITHER operates_in_eu OR operates_in_na is true.
- If neither: set operates_in_eu_or_na=false AND give a one-sentence
  reason_if_out_of_scope (e.g. "India-only fintech with no EU/NA offices per
  company website").
- confidence: "high" if multiple authoritative sources agree on size + region;
  "medium" if sparse/mixed; "low" if you guessed.
- sources: only URLs that appeared in your search results. No invented offices.
- A LinkedIn sales rep in a region is NOT operational presence — only count
  physical offices, regional job posts, or authoritative source statements.
- "Global"/"international" without country names is NOT evidence.
- A parent's location ≠ a subsidiary's location. Verify the target itself.
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
