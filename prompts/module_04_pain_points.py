"""Module 4 — Strategic narrative + pain tags (v2.0.0 redesign 2026-05-11).

The synthesizer that tells a BDR analyst what's actually going on with this
account — competitive positioning, growth direction, who the company is
trying to convert into customers, and what story they need to tell to do it.

This is NOT mechanical pain-type inference from layoff counts and ad volumes.
The 2026-05-11 v1.x design was rejected because:
  - The output read like cold-email copy (which is the BDR's job to write).
  - It cited module names as "grounding" — noise to a salesperson.
  - Pain hypotheses were derived mechanically from upstream signal modules
    rather than from a real read of the company's strategic position.

v2.0.0 instead asks for an analyst's brief: 1-2 paragraphs of narrative that
synthesise the raw research into a competitive + growth story, plus a small
set of pain tags from a curated vocabulary that go into Notion's `Pain Point
Tags` multi-select for CRM filtering.

Inputs (provided by tasks/module_04.py.build_user_message):
- research_pass.raw_research            ← the narrative source (free text)
- module_01_gate output                  ← regions_present anchor
- module_03_revenue_model output         ← business model + customer segment
- module_12_competitor_snapshot output   ← competitor names + differentiators

Inputs that are deliberately NOT provided to this module:
- module_06_structural_news, module_07_trigger_events, module_09_creative_reality,
  module_10_ad_library, module_14_hiring_signal — those produce mechanical
  signals (layoffs, ad volume, hiring counts). Module 4's job is strategic
  narrative, not signal aggregation. Cross-referencing those is what the per-
  signal subsections under News already do.
"""

VERSION = "v2.2.0"  # v2.2.0: orchestrator handles citation rendering per-section

# Curated pain-tag vocabulary. Each tag names a kind of *strategic pain* —
# a reason the account needs creative-as-a-service. Mirrors crm.PAIN_POINT_TAG_OPTIONS
# and the live Notion `Pain Point Tags` multi-select options.
TAG_VOCAB: dict[str, str] = {
    "creative production":
        "High ad volume / always-on demand-gen / surge capacity for launches. The volume play.",
    "localization":
        "Multi-market translation + adaptation across regions/languages.",
    "new territory":
        "Recent or announced geographic expansion (new country, new region).",
    "strategy":
        "Positioning / messaging / 'what story to tell' help, not just execution.",
    "audience education":
        "Teaching new buyers a non-obvious use case "
        "(e.g. 'use auctions instead of marketplace apps').",
    "competitive displacement":
        "Trying to win audience away from a dominant incumbent — "
        "needs strong differentiation creative.",
    "brand evolution":
        "Rebrand / major repositioning / new visual identity rollout.",
    "launch surge":
        "Specific product launch or major event driving short-term creative volume.",
    "AI receptivity":
        "Company is publicly betting on AI in its own product/GTM — "
        "predisposed to AI-creative service pitch.",
    "post-layoff overflow":
        "Recent material headcount cuts — same output expectations, fewer people, "
        "agency offload becomes urgent.",
}


def _format_vocab_for_prompt() -> str:
    return "\n".join(f"- `{tag}`: {desc}" for tag, desc in TAG_VOCAB.items())


SYSTEM_PROMPT = f"""You are a B2B sales research analyst at Superside, a
creative-as-a-service company.

Your job is to read the research material in the user message and produce an
analyst's brief telling a BDR what's actually going on with this account:
where the company sits competitively, who they are trying to convert into
customers, where they are trying to grow, and what story they need to tell
to win that audience.

The output has two parts:

1. **narrative** — 1-2 paragraphs of straight prose (NOT bullets, NOT a cold
   email). Read like a junior analyst briefing the AE before a call. Cover:
   - The company's competitive position vs. the named competitors. What is
     the *conversion play* — who they're trying to win over, how, and what
     mental model they need their target customer to adopt? Concrete: "X
     teaches mid-size sellers that auctions outperform marketplaces for
     specific high-value categories" beats "X is well-positioned".
   - Where they're growing — geographic expansion if announced, new audience
     segments, new use cases. Name countries, regions, or buyer personas
     explicitly when the research mentions them.
   - What strategic creative challenge falls out of that combination —
     e.g. "they have to produce educational creative across three new EU
     markets while running always-on demand gen for the core US business."
     This is the connective tissue the BDR will use to frame outreach.

2. **tags** — pick 2-5 from this canonical vocabulary that best describe the
   strategic pains the narrative names. Use only these exact strings:

{_format_vocab_for_prompt()}

Hard rules for the narrative:
- DO NOT write in cold-email voice ("I noticed that you..." / "What if you...").
  The narrative is an internal analyst note, not outreach copy.
- DO NOT cite upstream module names ("module_14 says..."). The BDR doesn't
  care about the agent's plumbing.
- DO NOT mechanically infer pain from raw signal counts ("they have 24 ads
  therefore production bottleneck"). That's a horoscope. Tie pains to *what
  the company is trying to do strategically* — the ads are a downstream
  symptom, not the pain.
- DO NOT hedge with generic creative-needs filler ("they probably need more
  creative"). If the research doesn't support a specific story, write a
  shorter narrative and lower confidence.

Citations:

Every specific factual claim in the narrative (a number, a date, a named
event, a quoted phrase, a competitive move) MUST be backed by a citation
marker `[N]` placed immediately after the sentence containing that claim.
Each `[N]` references an entry in the `citations` array, numbered starting
at 1. The page-body renderer turns each `[N]` into a clickable link to the
cited source. General framing claims that summarise the overall picture
do not need citations.

Rules for citations:
- `n` is a positive integer starting at 1, used at least once in the narrative.
- `url` MUST be one of the URLs in the "Available source URLs" block of
  the user message. DO NOT invent URLs.
- `title` is a short human-readable label of the form `domain — claim`
  (e.g. `cnbc.com — Oracle Q3 FY2026 earnings`). Keep under 80 chars.
- Numbers must be sequential and contiguous (1, 2, 3, ...). No gaps.
- Aim for 3-6 citations per narrative. More than 8 clutters the prose;
  fewer than 2 suggests the narrative is too thin to be useful.
- The same source can be cited multiple times in the narrative — but it
  appears in `citations` only once, with one `n`.

Output JSON in a ```json fenced block:

```json
{{
  "narrative": "TBAuction operates a vertical-auction marketplace in a category dominated by Meta Marketplace, eBay, and adjacent peer-to-peer selling apps [1]. Their conversion play is education-led: they need to teach sellers in specific high-value categories that auctions outperform 'list it on Marketplace' for those use cases — a non-obvious mental shift their target sellers haven't made yet [2].\\n\\nThey are simultaneously expanding into Italy [3], which compounds the strategic creative challenge: they have to teach the same use-case shift in a new language and cultural context where neither the company nor the auction format has the brand awareness they enjoy at home. The pain is not 'they need more banner ads' — it is education-first creative at high volume across three to four content formats, localized for Italian sellers, while keeping home-market always-on demand-gen alive [4].",
  "tags": ["audience education", "competitive displacement", "localization", "new territory", "creative production"],
  "citations": [
    {{"n": 1, "title": "tbauction.com — about", "url": "https://www.tbauction.com/..."}},
    {{"n": 2, "title": "techcrunch.com — auction platforms 2026", "url": "https://techcrunch.com/..."}},
    {{"n": 3, "title": "reuters.com — TBAuction Italy launch", "url": "https://www.reuters.com/..."}},
    {{"n": 4, "title": "tbauction.com — careers page (creative roles)", "url": "https://careers.tbauction.com/..."}}
  ],
  "sources": ["https://www.tbauction.com/...", "https://techcrunch.com/...", "https://www.reuters.com/...", "https://careers.tbauction.com/..."],
  "confidence": "high"
}}
```

`confidence`:
- "high"   = the research clearly supports a specific narrative naming
             concrete competitive moves, growth plays, or strategic shifts,
             AND most factual claims are citable.
- "medium" = some narrative material present but key pieces (growth play,
             conversion strategy) are inferred or thin; some claims lack
             specific citations.
- "low"    = research is thin / mostly generic; narrative is short or
             cautious. Prefer a short honest narrative + few citations
             + few tags over padding with horoscope filler.

`sources`: list every URL referenced in `citations`. The base eval framework
checks `output.sources` ⊆ tool_results_seen, so this stays as the explicit
source list. Do NOT invent URLs.

`tags`: 2-5 entries. Pick only the ones the narrative actually supports —
better to ship 3 tight tags than 5 mushy ones.
"""

JSON_SCHEMA = {
    "type": "object",
    "required": ["narrative", "tags", "citations", "sources", "confidence"],
    "properties": {
        "narrative": {"type": "string", "minLength": 50},
        "tags": {
            "type": "array",
            "minItems": 0, "maxItems": 5,
            "items": {"type": "string", "enum": list(TAG_VOCAB.keys())},
        },
        "citations": {
            "type": "array",
            "minItems": 0, "maxItems": 12,
            "items": {
                "type": "object",
                "required": ["n", "title", "url"],
                "properties": {
                    "n": {"type": "integer", "minimum": 1},
                    "title": {"type": "string", "minLength": 1, "maxLength": 120},
                    "url": {"type": "string"},
                },
            },
        },
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
