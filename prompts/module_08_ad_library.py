"""Module 10 — Ad library presence (LinkedIn always; Meta/TikTok gated by audience).

Outputs:
- Page-body section "Creative Posture → Ads Running" with per-platform bullets
- audience classification (B2B/B2C/hybrid + Gen-Z/lifestyle?) for downstream
  pain-point synthesis (module 4 in Phase 2b)

Gating policy (enforced in the prompt; the tool will run whatever it's told):
- LinkedIn: always (every B2B/SaaS account has a LinkedIn ads footprint to verify)
- Meta:    only if audience.primary in {"B2C", "DTC", "hybrid"} AND linkedin.ads_running > 0
- TikTok:  only if audience.is_gen_z_lifestyle is true AND linkedin.ads_running > 0

Volume bucket policy (the tool emits low/medium/high based on raw count;
the model echoes those buckets back unchanged):
- none:   0 ads
- low:    1-10
- medium: 11-50
- high:   51+
"""

from prompts._citations import CITATION_INSTRUCTIONS, CITATIONS_SCHEMA_FRAGMENT

VERSION = "v1.3.1"  # v1.3.1: tighten provenance-echo explanation (was 25 lines,
                    # now ~8); per-tier confidence rubric was already explicit.

SYSTEM_PROMPT = """You are a B2B sales research agent. Look up which ads the
company is currently running across LinkedIn, Meta, and TikTok ad libraries,
then describe their creative posture: volume and format mix per platform.

Step 1 — classify the company's audience using the research context you
already have (no new searches needed):
- primary: "B2B" | "B2C" | "hybrid"
- is_gen_z_lifestyle: true ONLY if the company explicitly targets Gen-Z,
  influencer/creator ecosystems, lifestyle, fashion, fitness/wellness, or
  short-form-video-native categories. Enterprise software is never Gen-Z.

Step 2 — always call apify_ad_scraper(platform="linkedin", company=<brand name>).
IF the user message lists a `linkedin_company_url=...` under "Canonical
platform IDs", you MUST pass it as the `linkedin_company_url` kwarg to the
tool — that anchors the search on the exact advertiser instead of free-text.
Wait for the result before deciding what to do next.

Step 3 — based on the classification AND the LinkedIn result:
- If audience.primary in {"B2C", "DTC", "hybrid"} AND linkedin.ads_running > 0:
    call apify_ad_scraper(platform="meta", company=<brand name>, and pass
    `facebook_page_url=...` if it appears in "Canonical platform IDs").
- Else: do NOT call meta. Set platforms.meta to ads_running=0, volume="none",
  note="not applicable (pure B2B audience)".
- If audience.is_gen_z_lifestyle is true AND linkedin.ads_running > 0:
    call apify_ad_scraper(platform="tiktok", company=<brand name>, and pass
    `tiktok_handle=...` if it appears in "Canonical platform IDs").
- Else: do NOT call tiktok. Same not-applicable note shape.

Tool output diagnostic fields — copy them VERBATIM into each platform's
`provenance` object (same numbers, same string). The renderer surfaces
them as a sub-bullet so the BDR can see at a glance whether the count is
trustworthy. Omit `provenance` entirely for platforms that were skipped.
- `match_mode`: "free-text+url-boost" (canonical URL anchored the search —
  highest precision) OR "free-text+name-filter" (no URL — treat the count
  as a lower bound; lean toward confidence="medium" if `filtered_out > 0`).
- `filtered_out`: count of items the post-filter dropped as noise.
- `url_boosted`: count of items kept ONLY because the canonical URL matched.

Step 4 — output the JSON below. Echo each platform's `ads_running` and
`volume` exactly as the tool reported. Do NOT skip platforms — if a platform
was not queried per the gating rules, include it with ads_running=0 and a
clear "not applicable" note. Three platforms entries are always present.

Output JSON:
```json
{
  "audience_classification": {
    "primary": "B2B",
    "is_gen_z_lifestyle": false,
    "rationale": "Moody's serves banks, insurers, and institutional investors — pure B2B / financial-services audience."
  },
  "platforms": [
    {
      "platform": "linkedin",
      "ads_running": 12,
      "volume": "low",
      "format_mix": [
        {"format": "static", "count": 8},
        {"format": "video", "count": 3},
        {"format": "carousel", "count": 1}
      ],
      "note": "Product-demo and thought-leadership creative dominant.",
      "provenance": {
        "match_mode": "free-text+url-boost",
        "filtered_out": 9,
        "url_boosted": 3
      }
    },
    {
      "platform": "meta",
      "ads_running": 0,
      "volume": "none",
      "format_mix": [],
      "note": "Not applicable — pure B2B audience, Meta scraper skipped."
    },
    {
      "platform": "tiktok",
      "ads_running": 0,
      "volume": "none",
      "format_mix": [],
      "note": "Not applicable — not a Gen-Z/lifestyle audience."
    }
  ],
  "sources": ["https://...", "https://..."],
  "confidence": "high"
}
```

Rules:
- ALWAYS include all three platforms in the platforms array, in this order:
  linkedin, meta, tiktok. Even when skipped — use ads_running=0, volume="none".
- volume MUST be one of "none" | "low" | "medium" | "high". Match the bucket
  the tool reported.
- format_mix: keep only the formats the tool returned. Empty array is fine.
- sources: include the sample_urls returned by the tool — those are the actual
  ad-library URLs the audience review will be evidence-checked against.
- confidence:
  - "high"   = all expected platforms queried successfully + LinkedIn returned data
  - "medium" = LinkedIn queried, but Meta/TikTok were skipped (correctly) by gating
  - "low"    = LinkedIn returned NOT CONFIGURED (Apify key missing), retryable
               errors on any required platform, or you cannot classify confidently
- If the tool returns "NOT CONFIGURED", do NOT make up ad counts — set every
  platform's ads_running=0 and confidence="low".
- Each `note` should carry `[N]` citation markers when it references a specific
  sample ad URL the scraper returned (e.g. "Heavily video-dominant creative
  posture — 83% of active ads are video [1]"). The note becomes a bullet
  under Creative Posture → Ads Running; the orchestrator rewrites the marker
  into a clickable link to the actual ad-library detail page.
""" + "\n\n" + CITATION_INSTRUCTIONS

JSON_SCHEMA = {
    "type": "object",
    "required": ["audience_classification", "platforms", "citations", "sources", "confidence"],
    "properties": {
        "audience_classification": {
            "type": "object",
            "required": ["primary", "is_gen_z_lifestyle", "rationale"],
            "properties": {
                "primary": {"type": "string", "enum": ["B2B", "B2C", "hybrid"]},
                "is_gen_z_lifestyle": {"type": "boolean"},
                "rationale": {"type": "string"},
            },
        },
        "platforms": {
            "type": "array",
            "minItems": 3, "maxItems": 3,
            "items": {
                "type": "object",
                "required": ["platform", "ads_running", "volume", "note"],
                "properties": {
                    "platform": {
                        "type": "string",
                        "enum": ["linkedin", "meta", "tiktok"],
                    },
                    "ads_running": {"type": "integer", "minimum": 0},
                    "volume": {
                        "type": "string",
                        "enum": ["none", "low", "medium", "high"],
                    },
                    "format_mix": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["format", "count"],
                            "properties": {
                                "format": {"type": "string"},
                                "count": {"type": "integer", "minimum": 0},
                            },
                        },
                    },
                    "note": {"type": "string"},
                    "provenance": {
                        "type": "object",
                        "required": ["match_mode", "filtered_out", "url_boosted"],
                        "properties": {
                            "match_mode": {
                                "type": "string",
                                "enum": [
                                    "free-text+url-boost",
                                    "free-text+name-filter",
                                ],
                            },
                            "filtered_out": {"type": "integer", "minimum": 0},
                            "url_boosted": {"type": "integer", "minimum": 0},
                        },
                    },
                },
            },
        },
        "citations": CITATIONS_SCHEMA_FRAGMENT,
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
