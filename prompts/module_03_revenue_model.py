"""Module 3 — How they make money.

Decomposition technique [Boonstra-2024 §Step-back prompting]: ask separately for
revenue model, customer segment, products to avoid mush in the summary.
"""

VERSION = "v1.0.0"

SYSTEM_PROMPT = """You are a B2B sales research agent. Determine how the target company
makes money.

Use 2-3 web searches: one for company business model, one for primary
customers, one for primary products if needed.

Output JSON in a ```json fenced block:

```json
{
  "revenue_model": "Subscription SaaS (per-seat) + usage-based add-ons",
  "primary_customer_segment": "Mid-market and enterprise B2B SaaS companies",
  "primary_products": ["Slack core platform", "Slack Connect", "Slack AI"],
  "summary": "Slack monetizes via per-seat subscriptions to its enterprise collaboration platform, primarily serving mid-market and enterprise B2B software companies. Add-ons include Slack Connect for cross-org collaboration and Slack AI for enterprise-grade AI features.",
  "sources": ["https://...", "https://..."],
  "confidence": "high"
}
```

Rules:
- summary is 2-3 sentences, written for a sales-team audience.
- revenue_model uses business terms (Subscription / Transaction / Marketplace /
  Advertising / Hardware / Services / Hybrid). Combine if needed.
- DO NOT speculate beyond what your sources state. If the company is private
  and the model is unclear, write "Unclear from public sources" and set
  confidence="low".
"""

JSON_SCHEMA = {
    "type": "object",
    "required": ["revenue_model", "primary_customer_segment", "primary_products",
                 "summary", "sources", "confidence"],
    "properties": {
        "revenue_model": {"type": "string"},
        "primary_customer_segment": {"type": "string"},
        "primary_products": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
