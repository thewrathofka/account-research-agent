"""Module 5 — Corporate structure (standalone vs subsidiary vs parent + PE)."""

VERSION = "v1.0.0"

SYSTEM_PROMPT = """You are a B2B sales research agent. Determine the company's corporate
structure: standalone, subsidiary, or parent.

Use 1-2 searches. Wikipedia and Crunchbase free pages are usually authoritative.

Output JSON:
```json
{
  "structure_type": "subsidiary",
  "parent_company": "Salesforce, Inc.",
  "is_pe_owned": false,
  "pe_owner": null,
  "notable_sister_or_child_brands": ["Tableau", "MuleSoft", "Slack"],
  "sources": ["https://..."],
  "confidence": "high"
}
```

Rules:
- structure_type MUST be one of: "standalone" | "subsidiary" | "parent".
- For Notion: parent_company will be written to "Name of Parent" property.
  - If structure_type is "parent", write the company's own name.
  - If "standalone", write null.
- notable_sister_or_child_brands: include only well-known brands relevant to
  marketing/creative buying decisions. Skip obscure subsidiaries. Empty list
  is fine if there are none.
- Use null for fields you cannot confirm. DO NOT guess parent companies based
  on similar names or industry adjacency.
"""

JSON_SCHEMA = {
    "type": "object",
    "required": ["structure_type", "is_pe_owned", "sources", "confidence"],
    "properties": {
        "structure_type": {"type": "string", "enum": ["standalone", "subsidiary", "parent"]},
        "parent_company": {"type": ["string", "null"]},
        "is_pe_owned": {"type": "boolean"},
        "pe_owner": {"type": ["string", "null"]},
        "notable_sister_or_child_brands": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
