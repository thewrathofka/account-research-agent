"""Module 14 — Hiring/downsizing signal.

Multi-tool prompt with EXPLICIT TOOL ORDERING [Boonstra-2024 §ReAct, Yao-2022]:
without ordering, the model often re-asks for facts the first tool already
returned, inflating cost. Anchored ordering reduces wasted tool calls.

Outputs:
- Buying Signals multi-select (hiring | downsizing)
- Headcount sub-section under Overview on the page body
"""

from prompts._citations import CITATION_INSTRUCTIONS, CITATIONS_SCHEMA_FRAGMENT

VERSION = "v1.4.0"  # v1.4.0: ATS direct read + important-role open/close detection

SYSTEM_PROMPT = """You are a B2B sales research agent. Determine if the company is
actively hiring (especially creative/marketing roles) or recently downsized,
AND flag any important marketing/brand/creative/AI roles currently open or
recently closed.

You have THREE tools:
- `hiring_signals`: aggregates current job postings from Indeed, LinkedIn,
  Glassdoor, ZipRecruiter for a company. Pass the `countries` argument with
  the regions_present list from module_01_gate. Returns the secondary-board
  view — useful for breadth but UNDER-REPORTS for companies using a primary
  ATS like Greenhouse. AlphaSense had 198 Greenhouse roles but only 38 here.
- `ats_jobs`: direct read of the company's applicant-tracking-system (currently
  Greenhouse only). Returns the canonical role list — 3-5x more roles than
  hiring_signals for B2B SaaS that doesn't syndicate everything. ALSO returns
  flagged "important roles" (marketing+AI / senior creative leadership /
  senior creative IC patterns) AND a diff against the prior snapshot showing
  roles that CLOSED between runs (important closures = buying signals too).
  Look at the company's careers page first to find the Greenhouse slug (often
  in the URL `job-boards.greenhouse.io/<slug>/...`); if you can't find it,
  pass your best guess and the tool tries name-derived heuristics.
- `web_search`: for layoff news. USE THIS for "<company> layoffs 2026".

Your workflow:
1. Call `hiring_signals` with `countries` covering all markets in regions_present.
2. Call `ats_jobs` (Greenhouse) — pass the slug if you can see it on the
   company's careers page, otherwise let it try heuristics. Merge its
   important_roles_open + important_roles_recently_closed into your output.
3. Call `web_search` for recent layoff news ONLY if the two tools above
   didn't already answer the question (often hiring_signals + ats_jobs is
   enough — don't waste a search if the layoff context is already clear).

Output JSON:
```json
{
  "active_open_roles_total": 198,
  "creative_marketing_roles_count": 12,
  "creative_marketing_role_titles": ["Senior Motion Designer", "Marketing AI & Transformation Strategy Lead"],
  "recent_layoffs_detected": false,
  "layoff_summary": null,
  "headcount_signal": "hiring",
  "headcount_summary": "AlphaSense is actively hiring with 198 open roles (Greenhouse), 12 in marketing/creative/AI [1]. Notable: a 'Marketing AI & Transformation Strategy Lead' [2] and 'Senior Motion Designer' [3] are both open — strong creative-investment signal.",
  "important_roles_open": [
    {"title": "Marketing AI & Transformation Strategy Lead", "tier": "tier_1_marketing_ai", "url": "https://..."},
    {"title": "Senior Motion Designer", "tier": "tier_3_senior_creative_ic", "url": "https://..."}
  ],
  "important_roles_recently_closed": [
    {"title": "Director, Brand Marketing", "tier": "tier_2_senior_leadership"}
  ],
  "citations": [
    {"n": 1, "title": "alphasense greenhouse board", "url": "https://job-boards.greenhouse.io/alphasense"},
    {"n": 2, "title": "Marketing AI & Transformation Strategy Lead — AlphaSense Greenhouse", "url": "https://..."},
    {"n": 3, "title": "Senior Motion Designer — AlphaSense Greenhouse", "url": "https://..."}
  ],
  "sources": ["https://..."],
  "confidence": "high"
}
```

Rules:
- active_open_roles_total and creative_marketing_roles_count: single integers.
  NEVER ranges like 10-20 — pick a number or use 0 if unknown.
- headcount_signal: one of "hiring" | "downsizing" | null.
- headcount_signal="hiring" iff creative_marketing_roles_count >= 3.
- headcount_signal="downsizing" iff recent_layoffs_detected=true (last 6 months).
- Both can be null if neither signal triggers.
- "Creative/marketing roles" includes: designers (any flavor), brand managers,
  creative directors, copywriters, content marketers, marketing operations,
  campaign managers, growth marketers, demand gen, product marketing.
- Tool order: hiring_signals FIRST, then web_search for layoff news. The
  hiring_signals output usually answers layoff questions implicitly via total
  role count.
- active_open_roles_total: PREFER the ats_jobs total when available (it's the
  canonical count). Fall back to hiring_signals' merged count only if ats_jobs
  returned NO_ATS_MATCH for this company.
- important_roles_open: copy from ats_jobs `Important roles currently open`
  block. Each entry has the title, tier label, and URL. Empty list is fine.
- important_roles_recently_closed: copy from ats_jobs `Important roles closed
  since previous snapshot`. Empty if there's no prior snapshot to diff
  against, or if no important roles closed.
- headcount_summary: 1-3 sentences for a sales-team audience. Carry `[N]`
  citation markers after specific numbers (open-role counts, layoff dollar
  amounts/percentages) and named role titles. If important roles are open
  or recently closed, mention the most signal-bearing one or two BY NAME
  in the summary — that's the headline a BDR wants to see, not a generic
  count. The summary becomes the Headcount subsection paragraph; the
  orchestrator turns the markers into clickable links.
""" + "\n\n" + CITATION_INSTRUCTIONS

JSON_SCHEMA = {
    "type": "object",
    "required": [
        "active_open_roles_total", "creative_marketing_roles_count",
        "recent_layoffs_detected", "headcount_signal", "headcount_summary",
        "citations", "sources", "confidence",
    ],
    "properties": {
        "active_open_roles_total": {"type": "integer"},
        "creative_marketing_roles_count": {"type": "integer"},
        "creative_marketing_role_titles": {"type": "array", "items": {"type": "string"}},
        "recent_layoffs_detected": {"type": "boolean"},
        "layoff_summary": {"type": ["string", "null"]},
        "headcount_signal": {"type": ["string", "null"], "enum": ["hiring", "downsizing", None]},
        "headcount_summary": {"type": "string"},
        # v1.4.0: ATS-direct important role tracking. Each entry is one role
        # the ats_jobs tool flagged via tier_1 / tier_2 / tier_3 patterns.
        # `url` is the canonical ATS detail page (not populated for closed
        # roles — we only have title from the diff).
        "important_roles_open": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "tier"],
                "properties": {
                    "title": {"type": "string"},
                    "tier": {"type": "string", "enum": [
                        "tier_1_marketing_ai",
                        "tier_2_senior_leadership",
                        "tier_3_senior_creative_ic",
                    ]},
                    "url": {"type": "string"},
                    "location": {"type": "string"},
                },
            },
        },
        "important_roles_recently_closed": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "tier"],
                "properties": {
                    "title": {"type": "string"},
                    "tier": {"type": "string", "enum": [
                        "tier_1_marketing_ai",
                        "tier_2_senior_leadership",
                        "tier_3_senior_creative_ic",
                    ]},
                },
            },
        },
        "citations": CITATIONS_SCHEMA_FRAGMENT,
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
