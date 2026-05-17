"""Module 4 — Strategic narrative + pain tags (v3.0.0 bullet redesign 2026-05-12).

v3.0.0 splits the narrative into per-pain bullets so the page-body
"Possible Pain Points" section is scannable instead of being a wall of prose.

The synthesizer that tells a BDR analyst what's actually going on with this
account — competitive positioning, growth direction, who the company is
trying to convert into customers, and what story they need to tell.

History:
- v1.x: mechanical pain-type inference from layoff counts / ad volumes.
  Rejected — read like a cold email, cited module names as grounding.
- v2.x: 1-2 paragraph narrative + tag bullet. Rejected — narrative was hard
  to scan on the page body when a BDR was prepping in two minutes before a call.
- v3.0.0 (now): brief intro line + per-pain bullets + tag bullet. Same
  analytical depth, scannable layout.

Inputs (provided by tasks/module_03.py.build_user_message):
- research_pass.raw_research            ← the narrative source (free text)
- module_01_gate output                  ← regions_present anchor
- module_02_revenue_model output         ← business model + customer segment
- module_09_competitor_snapshot output   ← competitor names + differentiators

Inputs that are deliberately NOT provided to this module:
- module_05_structural_news, module_06_trigger_events, module_07_creative_reality,
  module_08_ad_library, module_11_hiring_signal — those produce mechanical
  signals (layoffs, ad volume, hiring counts). Module 4's job is strategic
  narrative, not signal aggregation. Cross-referencing those is what the per-
  signal subsections under News already do.
"""

VERSION = "v3.0.0"  # v3.0.0: one bullet per pain point (replaces narrative paragraphs)

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

Your job is to read the research material in the user message and produce a
scannable analyst's brief telling a BDR what's actually going on with this
account: where the company sits competitively, who they are trying to convert
into customers, where they are trying to grow, and what story they need to
tell to win that audience.

The output has three parts:

1. **intro** — ONE sentence (or two short ones) of framing prose that sets
   up the company's strategic moment in plain English. NOT a cold email, not
   a tagline. Example: "Oracle is mid-pivot from on-prem database vendor to
   AI-infrastructure hyperscaler, racing AWS and Azure for the same set of
   enterprise AI workloads." This becomes the first paragraph in the page
   body before the bulleted pain points.

2. **pain_points** — a list of 2-5 STRATEGIC PAINS. Each is one bullet on
   the page body. Each item has:
   - `label`: 2-6 word headline naming the pain (e.g. "Category-perception
     battle vs. Meta Marketplace", "Multi-market expansion creative load",
     "Post-layoff creative overflow"). The label is what a BDR scanning
     the page in 10 seconds will read first — make it specific and named.
   - `body`: 1-3 sentences explaining the pain — what the company is trying
     to do strategically, who they're trying to convert, and why this is a
     creative/marketing challenge. Carry `[N]` citation markers immediately
     after sentences with verifiable claims (numbers, dates, named events,
     named competitors).

3. **tags** — pick 2-5 from this canonical vocabulary that best summarize
   the strategic pains your bullets named. Use only these exact strings:

{_format_vocab_for_prompt()}

Hard rules for the pain points:
- DO NOT write in cold-email voice ("I noticed that you..." / "What if you...").
  This is an internal analyst note, not outreach copy.
- DO NOT cite upstream module names ("module_14 says..."). The BDR doesn't
  care about the agent's plumbing.
- DO NOT mechanically infer pain from raw signal counts ("they have 24 ads
  therefore production bottleneck"). That's a horoscope. Tie pains to *what
  the company is trying to do strategically* — the ads are a downstream
  symptom, not the pain.
- DO NOT hedge with generic creative-needs filler ("they probably need more
  creative"). If the research doesn't support a specific named pain, OMIT
  it — fewer better pains beats more vague pains.
- DO NOT repeat the same pain twice with different labels. Each pain point
  in the list MUST name a distinct strategic challenge.

Citations:

Every specific factual claim in the intro and in each pain-point body (a
number, a date, a named event, a quoted phrase, a competitive move) MUST be
backed by a citation marker `[N]` placed immediately after the sentence
containing that claim. Each `[N]` references an entry in the `citations`
array, numbered starting at 1. The page-body renderer turns each `[N]` into
a clickable link to the cited source. General framing claims that summarise
the overall picture do not need citations.

Rules for citations:
- `n` is a positive integer starting at 1, used at least once in the brief.
- `url` MUST be one of the URLs in the "Available source URLs" block of
  the user message. DO NOT invent URLs.
- `title` is a short human-readable label of the form `domain — claim`
  (e.g. `cnbc.com — Oracle Q3 FY2026 earnings`). Keep under 80 chars.
- Numbers must be sequential and contiguous (1, 2, 3, ...). No gaps.
- Aim for 3-8 citations across intro + all pain bullets combined.
- The same source can be cited multiple times in the brief — but it appears
  in `citations` only once, with one `n`.

Output JSON in a ```json fenced block:

```json
{{
  "intro": "TBAuction runs a vertical-auction marketplace in a category dominated by Meta Marketplace, eBay, and adjacent peer-to-peer selling apps [1].",
  "pain_points": [
    {{
      "label": "Category-perception battle vs. Marketplace apps",
      "body": "Their conversion play is education-led: they need to teach sellers in specific high-value categories that auctions outperform 'list it on Marketplace' for those use cases — a non-obvious mental shift their target sellers haven't made yet [2]."
    }},
    {{
      "label": "Italy launch — new-market creative load",
      "body": "TBAuction is expanding into Italy [3], compounding the strategic creative challenge: they have to teach the same use-case shift in a new language and cultural context where neither the company nor the auction format has brand awareness."
    }},
    {{
      "label": "Always-on demand-gen across two markets",
      "body": "While ramping Italy, they still have to keep home-market always-on demand-gen alive [4] — the production load is now both higher-volume and more localized than before."
    }}
  ],
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
- "high"   = the research clearly supports specific named pains anchored in
             concrete competitive moves, growth plays, or strategic shifts,
             AND most factual claims are citable.
- "medium" = some pain material present but key pieces (growth play,
             conversion strategy) are inferred or thin; some claims lack
             specific citations.
- "low"    = research is thin / mostly generic; 1-2 cautious pains rather
             than padded filler.

`sources`: list every URL referenced in `citations`. The base eval framework
checks `output.sources` ⊆ tool_results_seen, so this stays as the explicit
source list. Do NOT invent URLs.

`pain_points`: 2-5 entries. Better to ship 2 tight pains than 5 mushy ones.
`tags`: 2-5 entries. Pick only the ones the pain points actually support.
"""

JSON_SCHEMA = {
    "type": "object",
    "required": ["intro", "pain_points", "tags", "citations", "sources", "confidence"],
    "properties": {
        "intro": {"type": "string", "minLength": 10},
        "pain_points": {
            "type": "array",
            "minItems": 1, "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["label", "body"],
                "properties": {
                    "label": {"type": "string", "minLength": 3, "maxLength": 120},
                    "body": {"type": "string", "minLength": 20},
                },
            },
        },
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
