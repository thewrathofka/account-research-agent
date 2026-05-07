# Account Research Agent

A monthly batch agent that researches B2B accounts in a Notion CRM ("All Accounts"
inside Money Moguls CRM) and writes structured findings + free-form research notes
back to each account's Notion page.

## Two tracks

This project exists in **two parallel forms** with different constraints:

### Track 1 — This repo (`~/code/account-research-agent/`) — *Company-shipping version*
- Standalone Python script. Plain Anthropic API + Tavily + Apify (planned).
- **Constraint: must be model-agnostic.** Will be handed to the Superside RevOps
  team, who may swap Claude for Gemini or OpenAI later. So:
  - No Claude-specific features (no extended thinking, no Claude-only tool patterns).
  - Tool-use loop and prompt structure should translate cleanly to other providers.
  - Cost model accounts for paid Anthropic tokens.
- Billed via the `ANTHROPIC_API_KEY` in `.env` (a *separate* product from Claude Max).

### Track 2 — Claude Code slash command (`~/.claude/commands/research-account.md`) — *Personal version*
- Runs inside Claude Code, uses Kali's Max subscription.
- Uses Claude Code's built-in `WebSearch`/`WebFetch` and Notion MCP.
- No per-token cost — unbounded prompt iteration.
- Mirrors the same MVP spec; can include modules deferred from the Python version.

The two tracks share the **same Notion schema, same module spec, same output format**
so research output is interchangeable.

---

## Current state (Track 1 — Python)

**File:** single-file MVP in `account_research_agent.py`. ~640 lines, 9 sections.

**What works:**
- Notion CRM read with Rep + Priority + `since` filter.
- Notion CRM write: properties + free-form blocks appended to page body.
- ReAct agent loop with Tavily web search.
- SQLite run log (`runs.db`) — one row per (account, task, run).
- Concurrent processing of accounts (default 5 in flight).
- Dry-run mode (no Notion writes).
- Confidence aggregation, status derivation (done / needs_review / failed / out_of_scope-pending).
- Anthropic SDK retries set to 8 for 529 resilience.

**What's tested:**
- ✅ Oracle live run wrote both properties and page blocks correctly.
- ❌ Batch of 7 hit Anthropic 529 storm — failed gracefully, no Notion writes.

**What's still placeholder:**
- The single task (`CompanyOverview`) uses a generic prompt and outputs an
  ad-hoc shape. To be replaced by the per-module tasks below.

---

## Full MVP spec (source of truth for both tracks)

Numbered as Kali specified. Items removed from MVP are listed at the bottom.

### 1. Size + EU/NA gate (GATE)
- Verify employee count; confirm operational presence in EU and/or NA.
- **Output:** Notion `Size` property (`<1000` / `1000-2000` / `2000-5000` / `5000+`).
- **GATE:** if neither EU nor NA, write *"No operations in EU or NA."* to notes,
  set `Research Status = out_of_scope`, **halt all subsequent modules.**
- Tool: Tavily (light — verifying existing CRM data, not deep research).

### 3. How they make money
- Revenue model + primary customer segment + primary products/SKUs.
- **Output:** 2-3 sentence summary on the page body under heading `Overview`.
- Tool: Tavily.

### 4. Possible pain points (synthesis)
- Synthesized hypotheses tying to Superside's value prop: production bottleneck,
  agency cost burn, hiring gaps, multi-market localization, post-layoff "same
  output fewer people," AI-creative receptivity.
- **Output:** 2-4 specific pain hypotheses, each grounded in concrete data, on
  page body under heading `Possible Pain Points`.
- Tool: Claude reasoning over modules 1, 3, 9, 10, 14 outputs (no fetch).

### 5. Corporate structure
- Standalone vs subsidiary vs parent. PE ownership if any.
- **Output:** Notion `Parent-Child` (text — sister/child companies if relevant)
  + `Name of Parent` (text — parent name; if standalone, leave empty; if itself
  the parent, write its own name).
- Tool: Tavily.

### 6. Structural news (last 6 months)
- M&A, spin-offs, mergers, IPO/SPAC, restructurings, layoffs, bankruptcy/distress,
  market exits.
- **Output:** Notion `Structure Notes` text property — short phrase like
  "recent IPO", "mass layoffs", "merged with X", "buying-frozen", "buying-friendly".
- Tool: Tavily news search.

### 7. Trigger events
- Funding rounds, active creative/marketing job posts, rebrand or campaign
  launches, agency RFP/switch news, AI initiative announcements.
- *(New marketing/brand/creative leader detection removed for MVP.)*
- **Output:** add tags to Notion `Buying Intent` multi-select property; write
  context detail to page body under heading `News` (under Possible Pain Points).
- Tool: Tavily news + Tavily search.

### 9. Current creative reality (lite — without persona-people data)
- Pain phrases mined from active job descriptions ("scale creative",
  "manage freelancers", "production bottleneck"). Active agency relationships.
- *(In-house team breakdown by function and team-to-marketing ratio removed —
  required module-2 LinkedIn employee data.)*
- **Output:** "Creative Posture" summary on page body under heading `News`.
- Tool: jobspy (free Python lib, aggregates Indeed/LinkedIn/Glassdoor/ZipRecruiter)
  + Tavily news for agency mentions.

### 10. Ad library
- What ads they're running, where, format mix, volume (last 12 months only).
- LinkedIn Ad Library always; Meta only after Claude classifies B2C/DTC/hybrid +
  count check > 0; TikTok only after classification as Gen-Z/lifestyle + count > 0.
- **Output:** page body under heading `Creative Posture` → sub-heading `Ads Running`,
  bullets per platform with ad type (static / motion / video / carousel) and
  volume (low / medium / high).
- Tool: Apify actors:
  - LinkedIn: `automation-lab/linkedin-ad-library-scraper`
  - Meta: `automly/facebook-ad-library-scraper` ($0.65/1k)
  - TikTok: Apify TikTok ad scraper

### 12. Competitor snapshot
- Top 3 direct competitors + short note on news/marketing differentiation.
- **Output:** page body under heading `Competitor Landscape`.
- Tool: Tavily.

### 13. Industry pulse
- 2-3 recent category-level stories (last 60 days).
- **Output:** appended to `Competitor Landscape` as a paragraph. If relevant,
  add `industry movement` to `Buying Intent` property.
- Tool: Tavily news.

### 14. Hiring or downsizing
- Active hiring (especially creatives/marketing) or layoffs.
- **Output:** add `hiring` or `downsizing` to `Buying Signals` property; write
  1-2 sentence summary on page body under heading `Headcount` (sub-section of `Overview`).
- Tool: jobspy + LinkedIn jobs (Apify, optional) + Tavily news.

### Modules removed from MVP
- **2** persona-relevant people count (Apify LinkedIn employee scraper — too expensive)
- **8** buying committee (depended on 2)
- **11** top contact deep-dives (depended on 8)
- **"New marketing/brand/creative leader (last 90 days)"** sub-trigger of module 7

---

## Notion schema

### Existing
| Property | Type | Used by |
|---|---|---|
| `Account Name` | title | input |
| `Rep` | select | filter |
| `Priority Type` | select | filter |
| `Size` | select (`<1000`/`1000-2000`/`2000-5000`/`5000+`) | module 1 |
| `Buying Signals` | multi-select | modules 14 (`hiring`/`downsizing`) |
| `Lead Signal` | multi-select | (not used by agent) |
| `New Hire` | text | (not used by agent — module removed) |
| `Company Structure ` | text (note: trailing space) | (manual / future use) |
| `Parent-Child` | text | module 5 (sister / child names) |
| `Name of Parent` | text | module 5 |
| `Last Researched` | date | agent metadata |
| `Research Confidence` | select (`high`/`medium`/`low`/`failed`) | agent metadata |
| `Research Status` | select (`pending`/`done`/`needs_review`/`failed`) | agent metadata |

### Planned additions
| Property | Type | Used by |
|---|---|---|
| `Structure Notes` | text | module 6 |
| `Buying Intent` | multi-select | modules 7 + 13 |
| `Research Status` (new option) | add `out_of_scope` | module 1 gate |

`Buying Intent` options to create:
`funding round`, `active creative jobs`, `rebrand/campaign`, `agency switch`,
`AI initiative`, `industry movement`.

### Page body section order (left-to-right top-to-bottom)
1. `Overview` — module 3
2. `Headcount` (sub-section of Overview) — module 14
3. `Possible Pain Points` — module 4
4. `News` — module 7
5. `Creative Posture` — module 9
6. `Ads Running` (sub-section of Creative Posture) — module 10
7. `Competitor Landscape` — modules 12 + 13

---

## Tools & costs (Track 1 — Python)

### In use
- **Tavily** — broad web search, news, Wikipedia-grade facts. ~$0.008/search,
  1k/mo free.
- **Anthropic API** — model inference. Sonnet 4.5 (~$3/M input, $15/M output).

### Phase 2 additions
- **Apify** — LinkedIn ad library, Meta ad library, TikTok ad library. Pay-per-use.
- **jobspy** (free Python lib) — module 14 + module 9 JD scraping.

### Skipped
- Firecrawl (deemed redundant given jobspy + Tavily covers Phase 1).
- LinkedIn employee scrapers (modules 2/8/11 cut from MVP).
- Crunchbase, ZoomInfo, BuiltWith, Apollo, Hunter.io (over-spec for MVP).

### Cost picture (monthly, tools only — Anthropic tokens additional)
| Scope | Phase 1 (Tavily + jobspy) | Full MVP (+ Apify ad libs) |
|---|---|---|
| 8 Priority-A | $1-3 | $4-12 |
| 213 (Kali's pipeline) | $25-80 | $100-300 |
| 1,255 (full team) | $150-500 | $600-1,800 |

### Cost gates
- **Gate (module 1):** if EU/NA fails, halts pipeline → ~$0.04/account total cost.
- **`MAX_AGENT_ITERATIONS = 10`** per task.
- **`TAVILY_SEARCHES_PER_TASK_CAP = 8`** per task.

---

## Phase plan

### Phase 1 — Tavily + jobspy only ($0.10-0.30/account)
Modules: **1, 3, 5, 6, 7 (minus new-leader), 9 (jobspy only), 12, 13, 14.**
Ships ~80% of the MVP without Apify. **Demo-able on Kali's 8 accounts.**

### Phase 2 — Add Apify ad libraries
Modules: **4 (synthesis), 10 (LinkedIn always; Meta/TikTok gated by classification).**

### Phase 3 — Hardening for team handoff
- Cost ceilings per account (`--max-cost`).
- Resume on partial failure (per-task, not just per-account).
- Provider abstraction layer (so Gemini/OpenAI swap is a one-file change).
- Monthly scheduled run (cron or `/schedule` skill).

---

## Key constraints

1. **Track 1 must be model-agnostic.** No Claude-only features. The agent loop
   uses tool-use semantics that map cleanly to OpenAI's function-calling and
   Gemini's tool-use.
2. **Wrong data is worse than missing data.** Confidence-gated writes — `low`
   confidence sets `Research Status = needs_review`, no live property updates
   to fields the team relies on. Already enforced in the orchestrator.
3. **Resumable.** `Last Researched` + `--since N` gives idempotent monthly runs.
4. **Nothing written on failure.** Failed tasks log to SQLite but never write
   to Notion (verified during the 7-account 529 run).

---

## Open decisions

1. **Schema additions waiting approval** — `Structure Notes`, `Buying Intent`,
   `out_of_scope` Research Status option. (Asked, not yet confirmed.)
2. **Salesforce size band mismatch** — Notion has `1000-2000`, Salesforce
   doesn't. Deferred — sync mapping problem, not relevant to research.
3. **Apify account** — not yet created. Needed for Phase 2.

---

## File map

```
account-research-agent/
├── account_research_agent.py   ← single-file MVP (current shape)
├── CLAUDE.md                   ← this file
├── requirements.txt
├── runs.db                     ← SQLite run log (gitignored)
├── .env                        ← API keys (gitignored)
└── venv/
```

Personal version lives at `~/.claude/commands/research-account.md`.

---

## Pointers

- **Run dry:** `python account_research_agent.py --limit 1 --dry-run`
- **Run live:** `python account_research_agent.py --limit 1`
- **Run batch (skip recently researched):** `python account_research_agent.py --since 30`
- **Inspect last run output:**
  `python -c "import sqlite3,json; c=sqlite3.connect('runs.db'); r=c.execute('select output_json from task_runs order by id desc limit 1').fetchone(); print(json.dumps(json.loads(r[0]), indent=2))"`
- **Notion All Accounts DB ID:** `6d510b5a-9c8f-490f-8600-429184341edc`
- **Money Moguls CRM parent page:** `3523435e-7148-8111-b9be-df6e2c5844b9`
