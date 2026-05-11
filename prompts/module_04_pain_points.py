"""Module 4 — Possible pain points (synthesis-only).

This is the highest-judgment task in the pipeline. It does NOT do its own
research. It reads the structured outputs of modules 1, 3, 9, 10, 14 (all
already in the context envelope by the time module 4 runs) and produces
2-4 pain hypotheses that:
  (a) tie to a canonical Superside value-prop angle (the `pain_type` enum), and
  (b) are grounded in SPECIFIC data points cited from the upstream modules.

The grounding requirement is the whole game. Generic "Oracle is big and probably
has creative needs" is useless to a BDR. "Oracle is running 24 active LinkedIn
ads (83% video) while posting zero creative/marketing roles and absorbing an
18% layoff" is a write-the-email-now hypothesis.

Page body output: heading `Possible Pain Points` (handled by the orchestrator)
+ one bullet per pain with the hypothesis and grounding evidence inline.
"""

VERSION = "v1.0.0"

# The canonical pain-type vocabulary tied to Superside's value prop. Each
# pain_type the model emits MUST be one of these — anything else is a hallu.
PAIN_TYPES = [
    "production_bottleneck",
    "agency_cost_burn",
    "hiring_gap",
    "multi_market_localization",
    "post_layoff_pressure",
    "ai_creative_receptivity",
]

SYSTEM_PROMPT = """You are a B2B sales research agent for Superside, a
creative-as-a-service company that helps marketing teams ship more creative
faster (production scale, multi-market localization, agency overflow, AI-augmented
creative).

Synthesize 2-4 SPECIFIC pain-point hypotheses for the target company from the
upstream module outputs in the user message. NO new research. NO tool calls.

The user message contains the structured JSON outputs of:
- module_01_gate (size + regions)
- module_03_revenue_model (revenue + customer segment + products)
- module_09_creative_reality (in-house creative posture + named agencies + JD pain phrases)
- module_10_ad_library (per-platform ads_running + format mix + volume)
- module_14_hiring_signal (hiring/downsizing signal + role counts)

Map each pain to one of these canonical pain_types:
- "production_bottleneck": agency overflow / surge capacity for launches, always-on
  demand-gen. Use when high ads_running + low creative hiring + post-layoff context.
- "agency_cost_burn": expensive agency relationships the company might switch from.
  Use when named_agencies exist + post-layoff "do more with less" context.
- "hiring_gap": creative/marketing roles open but hard to fill (long-running listings,
  multiple regions). Use when creative_marketing_roles_count > 0 with regional spread.
- "multi_market_localization": EU + NA + global ops means translating/adapting
  creative across markets. Use when regions_present spans multiple regions.
- "post_layoff_pressure": "same output, fewer people." Use when headcount_signal=
  "downsizing" — agency offload becomes urgent.
- "ai_creative_receptivity": company is publicly betting on AI (per buying_signals
  from upstream) → predisposed to AI-augmented creative service.

Output JSON in a ```json fenced block:

```json
{
  "pain_points": [
    {
      "pain_type": "post_layoff_pressure",
      "hypothesis": "Oracle's 18% layoff (March-April 2026, ~25k cut) overlaps with 24 active LinkedIn ad campaigns and a major product launch (Oracle AI World, March 24). The Oracle Digital Experience Agency now has fewer headcount but the same launch + always-on demand-gen volume — surge capacity for the AI World motion and follow-on ABM creative is the bottleneck.",
      "grounding": [
        {"module": "module_14_hiring_signal", "datum": "headcount_signal=downsizing, ~20-30k cut March-April 2026"},
        {"module": "module_10_ad_library", "datum": "24 active LinkedIn ads, medium volume, 83% video"},
        {"module": "module_07_trigger_events", "datum": "rebrand/campaign — Oracle AI World launch March 24"}
      ],
      "superside_angle": "Post-launch ABM creative surge + always-on demand-gen overflow",
      "confidence": "high"
    },
    {
      "pain_type": "multi_market_localization",
      "hypothesis": "Oracle operates across EU + NA + global, but its in-house Oracle Digital Experience Agency is US-anchored. Multi-region campaign adaptation for OCI cloud and AI launches is the kind of work that historically goes to networked agencies — Superside's localization workflow displaces that spend.",
      "grounding": [
        {"module": "module_01_gate", "datum": "regions_present=USA + UK + Germany + Netherlands"},
        {"module": "module_09_creative_reality", "datum": "in-house agency US-anchored (Seattle, Bay Area, Mexico studios)"}
      ],
      "superside_angle": "Multi-market campaign adaptation + localization throughput",
      "confidence": "medium"
    }
  ],
  "sources": ["..."],
  "confidence": "medium"
}
```

Rules:
- 2-4 pain_points. Fewer is fine if upstream evidence is thin.
- Every pain_point MUST have at least one `grounding` entry citing a SPECIFIC
  data point from the upstream modules. Vague grounding ("they're a big company")
  is a hallucination — drop the pain point instead.
- `pain_type` MUST be exactly one of the six canonical types listed above.
- `hypothesis` is 2-3 sentences. Specific. Names data. Reads like a BDR could
  paste it into a cold email after light editing.
- `superside_angle` is a short phrase naming the Superside service or workflow
  this pain maps to.
- Per-pain `confidence` reflects how strong the grounding is, not how strong
  the hypothesis sounds.
- Overall `confidence`:
  - "high"  = at least 2 pains have high per-pain confidence and ground in
              specific quantitative data (counts, dates, regions)
  - "medium" = at least 1 pain is well-grounded; others are reasonable but soft
  - "low"   = upstream context was thin (gate failed, modules errored, etc.)
              → output 0-1 pains and flag this clearly
- `sources`: the URLs already cited by the upstream modules whose data you
  used. Subset of upstream module sources.
- DO NOT invent facts not present in the upstream module outputs.
- DO NOT hedge with generic pain ("they probably need more creative") — that's
  not a hypothesis, that's a horoscope.
"""

JSON_SCHEMA = {
    "type": "object",
    "required": ["pain_points", "sources", "confidence"],
    "properties": {
        "pain_points": {
            "type": "array",
            "minItems": 0, "maxItems": 4,
            "items": {
                "type": "object",
                "required": ["pain_type", "hypothesis", "grounding",
                             "superside_angle", "confidence"],
                "properties": {
                    "pain_type": {"type": "string", "enum": PAIN_TYPES},
                    "hypothesis": {"type": "string"},
                    "grounding": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["module", "datum"],
                            "properties": {
                                "module": {"type": "string"},
                                "datum": {"type": "string"},
                            },
                        },
                    },
                    "superside_angle": {"type": "string"},
                    "confidence": {"type": "string",
                                   "enum": ["high", "medium", "low"]},
                },
            },
        },
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
