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

## Current state (Track 1 — Python, post-Phase 2 + ATS upgrade, 2026-05-11)

**All shipped phases:**
- ✅ Phase 1 (v0.1.0): the 9 Tavily+jobspy modules
- ✅ Phase 1.5a (v0.2.0): model tiers, prompt caching, tool dedup, shared research context
- ✅ Phase 1.5b (v0.3.0): Anthropic Batch API + v1.1.0 prompt trim
- ✅ Fix Appendix (v0.4.0, 2026-05-09→11): 22 items + 3 corrections — see
  `sessions/2026-05-09-fix-appendix-implementation.md`
- ✅ Phase 2a (2026-05-11): module 10 ad library + Apify integration
- ✅ Phase 2b (2026-05-11): module 4 strategic narrative + Pain Point Tags
- ✅ Citation rollout (2026-05-11): orchestrator-side per-section renumbering,
  applied to all 10 page-body modules
- ✅ Module 14 v1.5.0 (2026-05-11): Greenhouse ATS direct read +
  important-role open/close detection + location-aware hiring signal

**11 task modules registered** (`tasks/__init__.py`):
research_pass, module_01_gate, module_03, module_04, module_05, module_06,
module_07, module_09, module_10, module_12, module_13, module_14.
Module 4 runs LAST because it synthesizes from modules 1, 3, 12 outputs.

**Cost picture (per account, Anthropic provider):**
| Version | Per account | Notes |
|---|---|---|
| v0.0.0 / v0.1.0 | $1.37 | placeholder / 9 real prompts, no cost optimization |
| v0.2.0 | ~$0.27 | model tiers + dedup + shared research context |
| v0.3.0 | ~$0.20 | + v1.1.0 prompts (trimmed) + Batch API for synthesis |
| **v0.4.0 + Phase 2 (now)** | **~$0.20–0.30** | adds modules 4 + 10 + ATS; Apify free-tier covers tool cost |

Cross-provider proof of swap (live-measured, v0.3.0 era):
- module_01_gate v1.1.0 on Anthropic Sonnet 4.6: 100% schema/calib/sources, 91% coverage
- module_01_gate v1.1.0 on OpenAI gpt-5.5: 100% schema/calib/sources, 94% coverage
- ~47% cheaper per call on OpenAI for this task
- One-line swap via `--provider openai`

Open follow-ups:
- Full 12-task pipeline cross-provider eval (only module_01_gate has been
  cross-provider tested live; gpt-5.5 pricing in `config.py` is still a
  placeholder — re-pin once OpenAI ships final rates before trusting any
  scale-cost forecasts on `--provider openai`)
- OpenAI Batch API (file-upload flow; Anthropic Batch shipped)
- Real Gemini provider (still a stub)
- Per-prompt golden cases for modules 3, 4, 5, 6, 7, 9, 10, 12, 13, 14, 15
  (currently only module_01_gate + a handful of others have goldens)
- Lever / Ashby / Workday ATS providers (Greenhouse-only today)
- Phase 3 hardening: cost ceilings per account, per-task resume, monthly
  scheduled cron

Model tiers (current): two tiers, NOT five. `smart` (Sonnet/gpt-5.5/Pro)
for tool-using tasks; `fast` (Haiku/gpt-5.5-mini/Flash) for synthesis. See
`config.MODEL_PRICING_PER_M_TOKENS` for the per-model rates and a note on
why `cached_input` semantics differ across providers.

Companion docs: `INVENTORY.md` is the manual-verification catalogue of every
module + every Notion property + every page-body section. `AUDIT.md` is the
2026-05-13 pre-lockdown code review with findings (most fixed; C2 was a
false positive on enum-null, reverted).

---

## Citation system (orchestrator-side, all page-body modules)

Each module's prompt instructs the model to emit `[N]` markers in page-body
text + a `citations: [{n, title, url}]` array. The orchestrator's
`_research_section_blocks`:

1. Walks every task contributing to a page-body section in display order.
2. Renumbers their citations globally per section starting at 1 (dedupes
   same-URL entries across modules).
3. Rewrites `[N]` markers in each task's blocks into clickable Notion
   rich_text link spans pointing at the renumbered citation URL.
4. Emits one footnote bullet list at the end of each section
   (`Sources:` lead paragraph + one bullet per citation, leading `[N]`
   clickable).

Shared instruction text lives in `prompts/_citations.py`. Per-signal
subsections under News (rendered from `TaskResult.signal_sections`) keep
their existing per-signal URL bullets — they're evidence dumps, not narrative.

Module 4 follows the same contract — its prior `to_blocks` self-rendering
was moved to the orchestrator on 2026-05-11 (v2.1.0 → v2.2.0). The
canonical reference for the contract is `prompts/_citations.py` and the
implementation is in `orchestrator.py`.

---

## Module 14 — ATS direct read + location-aware hiring signal

v1.5.0 (2026-05-11): jobspy was systematically under-reporting for B2B
SaaS accounts (AlphaSense: 38 USA roles via jobspy vs 198 globally on
Greenhouse). Module 14 now uses three tools:

1. `hiring_signals` (jobspy) — secondary boards, breadth check
2. `ats_jobs` (Greenhouse public API) — ATS direct, canonical for B2B SaaS
3. `web_search` — only when needed for layoff news

`ats_jobs` (`tools/ats_fetcher.py`) does three things:

**Important-title classification.** A three-tier regex bank flags
strategically-relevant roles:
- Tier 1 — marketing/brand/creative/content/design × AI/ML/automation/
  transformation (Kali's explicit ask)
- Tier 2 — Head/VP/Director/Chief/CMO/CCO/SVP in marketing/brand/creative/
  content/design/growth/demand-gen/RevOps; plus level-implicit titles
  (Creative Director, Art Director, CMO standalone)
- Tier 3 — Senior/Principal/Staff/Lead designer/motion designer/brand
  designer/copywriter/content strategist
First-match-wins so Tier 1 doesn't double-count as Tier 2.

**Snapshot diff.** Every fetch is stored in SQLite (`ats_snapshots` table).
On next run for the same company, the tool diffs against the prior snapshot
and surfaces important roles that CLOSED between runs. Closures of senior
creative/marketing roles = buying signal (the company just hired and is
ramping creative ops).

**Location-aware signal.** Each important role's location is classified via
`is_in_scope_location()`:
- In-scope: UK + EU + NA (Superside GTM markets)
- Out-of-scope: India, APAC, ME, etc.
- Unknown: no country qualifier present (Remote, TBD)

Module 14 schema has `creative_marketing_roles_in_scope_count` separate
from the global count. The `hiring` Buying Signal triggers iff
`in_scope_count >= 3`. Out-of-scope creative hiring still appears in the
Headcount page body for context but doesn't trigger the signal.

---

## Current state (Track 1 — Python, v0.1.0 = end of Phase 1)

**Structure:** package layout under `~/code/account-research-agent/`. The file
`account_research_agent.py` is now a ~110-line CLI entry point; everything
substantial lives in sibling modules (`config.py`, `crm.py`, `run_log.py`,
`orchestrator.py`, `providers/`, `tools/`, `tasks/`, `prompts/`, `evals/`,
`tests/`). See `README.md` §Architecture for the file map.

**Phase 1 modules shipped (all v1.0.0):**
- `module_01_gate` — Size + EU/NA gate (halts pipeline on fail)
- `module_03_revenue_model` — revenue model + customers + products
- `module_05_corporate_structure` — standalone/subsidiary/parent + PE
- `module_06_structural_news` — last-6mo events with constrained vocab
- `module_07_trigger_events` — funding / rebrand / agency switch / AI initiatives
- `module_09_creative_reality` — JD pain phrases + named agencies
- `module_12_competitor_snapshot` — top 3 direct competitors
- `module_13_industry_pulse` — last-60d category stories
- `module_14_hiring_signal` — hiring/downsizing via jobspy + Tavily

**Provider abstraction:** Anthropic + OpenAI both fully implemented; Gemini
stubbed. Swap is one line via `PROVIDER` env var or `--provider` flag.

**Eval system:** `python -m evals.runner --task module_NN [--all]`. Five
metrics (E1–E5: schema / coverage / calibration / sources / cost). Three
golden cases seeded (Stripe, Oracle, Razorpay). Module 1 baseline run was
3/3 PASS at 100% schema/calibration/sources, 94% field coverage.

**Tests:** 24 pytest cases covering provider abstraction, prompt loading,
gate logic, section assembly, and metrics scoring.

**Outstanding:**
- 5+ more golden cases need hand-annotation (plan §7.2: 7 Priority-A
  accounts + 1 negative).
- OpenAI swap proof-of-concept run (needs `OPENAI_API_KEY`).
- Phase 2 modules: 4 (pain-point synthesis), 10 (ad libraries — Apify).
- Phase 3 hardening: per-task resume, scheduled monthly cron, Gemini impl.

---

## Old state (kept for reference — v0.0)

The original v0.0 was a single-file MVP (~640 lines) with one placeholder task
(`CompanyOverview`) and direct Anthropic SDK calls. Verified live on Oracle
(2026-05-06). Hit a 529 storm on batch run; failed gracefully (no Notion writes).
Refactored into the v0.1.0 package layout. `_legacy.py` files preserve the
v0.0 behaviour for offline replay.

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
- **Output:** Notion `sister/child` (text — sister/child companies if relevant)
  + `Parent` (text — parent name; if standalone with no children, leave empty;
  if itself a parent or owns notable child brands, write its own name).
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

### Current Notion schema (post-2026-05-11 migration)

| Property | Type | Used by | Owner |
|---|---|---|---|
| `Account Name` | title | input | (system) |
| `Rep` | select | filter | manual |
| `Priority Type` | select | filter | manual |
| `Size` | select (`<1000`/`1000-2000`/`2000-5000`/`5000+`) | module 1 | agent |
| `Buying Signals` | multi-select | modules 7 + 13 + 14 | **agent-only** |
| `Buying Intent` | multi-select | (filter / curation) | **manual-only** |
| `Pain Point Tags` | multi-select | module 4 | **agent-only** |
| `Lead Signal` | multi-select | (not used by agent) | manual |
| `New Hire` | url | (not used by agent — module removed) | manual |
| `Company Structure` | text | (manual / future use) | manual |
| `sister/child` | text (renamed from `Parent-Child` 2026-05-12) | module 5 (sister / child brands) | agent |
| `Parent` | text (renamed from `Name of Parent`) | module 5 (parent name / self if standalone-parent / empty) | agent |
| `Structure Notes` | text | module 6 | agent |
| `Last Researched` | date | agent metadata | agent |
| `Research Confidence` | select (`high`/`medium`/`low`/`failed`) | agent metadata | agent |
| `Research Status` | select (`pending`/`done`/`needs_review`/`failed`/`out_of_scope`) | module 1 gate + metadata | agent |
| `Notes` | text | (not used by agent) | manual |

### Multi-select option vocabularies

`Buying Signals` (agent-only — overwrite semantics, empty multi_select clears stale tags):
`funding round`, `active creative jobs`, `rebrand/campaign`, `agency switch`,
`AI initiative`, `industry movement`, `hiring`, `downsizing`.

`Buying Intent` (manual-only — agent never writes):
the BDR's curated tags: `MQA`, `unify high`, `unify mod`, `sales nav high`,
`sales nav mod`, `industry`, `cluster`, `CW competitor`. (Legacy `hiring` /
`downsizing` options exist as residue from before the 2026-05-11 role swap;
agent does not write them.)

`Pain Point Tags` (agent-only — module 4):
`creative production`, `localization`, `new territory`, `strategy`,
`audience education`, `competitive displacement`, `brand evolution`,
`launch surge`, `AI receptivity`, `post-layoff overflow`.

### 2026-05-11 schema migration notes

- **Buying Signals / Buying Intent role swap.** Before this date `Buying
  Signals` was dual-purpose (agent writing `hiring`/`downsizing` mixed with
  human MQA/unify/etc. tags). The agent's writes were silently leaving
  stale tags across reruns (e.g. AlphaSense's `downsizing` from May 9 still
  sitting on the page on May 11 even though the new run found no layoffs).
  Resolved structurally: agent owns `Buying Signals` 100%, humans own
  `Buying Intent` 100%. Agent's writeback now always includes the property
  (default empty multi_select) so stale tags clear on rerun.
- **`Name of Parent` → `Parent` rename.** Same field, shorter name.
- **`Pain Point Tags` populated.** Was a placeholder with `TBD - populate`;
  now has the 10-tag vocabulary above as selectable options.

### 2026-05-12 follow-up migrations

- **`Parent-Child` → `sister/child` rename.** Same field, more accurate
  name (the property stores sister/child brand names, not the parent).
  Code constant `PROP_PARENT_CHILD` → `PROP_SISTER_CHILD`.
- **Module 5 Parent self-name bug fix.** The model frequently classifies a
  parent-with-one-child company as `structure_type=standalone` (e.g.
  AlphaSense after acquiring Tegus). `to_fields()` now treats any non-empty
  `notable_sister_or_child_brands` as evidence that the company is a parent
  and writes its own name to `Parent`. Prompt v1.3.0 also tightens the
  classification rule so child brands force `structure_type="parent"`.
- **Module 6 placeholder-name fix.** Prompt v1.4.0 hardens the rule that
  `merged with X` / `acquired Y` placeholders must be substituted with
  actual company names from the research, or set `structure_note=null`.
  Also clarifies when to use `out of business` (full sunset after
  acquisition / Chapter 7 / cease-of-operations).
- **Module 13 90d window + Superside-relevance filter.** Prompt v1.4.0
  expands the recency window from 60d to 90d and tightens the relevance
  filter: a story qualifies only if it would plausibly affect creative/
  marketing strategy or wider category buying willingness — not generic
  industry news.
- **Module 4 v3.0.0 — bullet layout.** Replaces the v2.x narrative-paragraph
  output with `intro` (one-line frame) + `pain_points[]` (one bullet per
  named pain) + tag bullet. Same analytical depth, scannable in 10 seconds.
  Schema: `{intro, pain_points:[{label, body}], tags, citations, sources}`.
  Backwards-compat path: cached v2.x outputs (with `narrative` field) still
  render as paragraph blocks.
- **Citation system simplified.** Orchestrator no longer emits the per-section
  "Sources:" footnote bullet list OR the global page-bottom "Sources"
  heading_3. The inline `[N]` clickable links carry all the source info —
  the footnote duplication was visual noise.
- All schema changes were performed live via Notion MCP `ALTER COLUMN`.
  Code's `EXPECTED_NOTION_PROPERTIES` constant (`crm.py`) is the contract;
  the agent's `validate_schema()` call at startup halts the run if the
  Notion schema drifts from this expectation.

### Page body section order (top-to-bottom)
1. `Overview` — modules 3 + 5
2. `Headcount` (subsection of Overview) — module 14 + important-role bullets
3. `Possible Pain Points` — module 4 (narrative + Pain Point Tags bullet)
4. `News` — module 7 + per-signal subsections (`### funding round`,
   `### industry movement`, `### hiring`, `### downsizing`, etc.) from
   modules 7, 13, 14
5. `Creative Posture` — module 9
6. `Ads Running` (subsection of Creative Posture) — module 10
7. `Competitor Landscape` — modules 12 + 13

Each section ends with a `Sources:` footnote bullet list rendered by the
orchestrator's per-section citation pass (see "Citation system" above).
The page also has one global `Sources` heading_3 at the very bottom
listing every URL the agent saw (catch-all for modules that haven't been
migrated to citations yet — currently all 10 page-body modules emit
citations, so this is largely redundant but kept as a safety net).

---

## Tools & costs (Track 1 — Python)

### In use
- **Tavily** — broad web search, news, Wikipedia-grade facts. ~$0.008/search,
  1k/mo free.
- **Anthropic API** — model inference. Sonnet 4.6 (~$3/M input, $15/M output).
- **jobspy** (free Python lib) — module 9 + 14 secondary-board scraping
  (Indeed + LinkedIn; ZipRecruiter 403s and Glassdoor errors are known
  issues — reduced coverage in practice).
- **Greenhouse public API** — module 14's `ats_jobs` tool. Free, no auth.
  198 open roles returned for AlphaSense vs jobspy's 38 — canonical for B2B
  SaaS that uses Greenhouse.
- **Apify** — module 10's `apify_ad_scraper` tool. LinkedIn (free) + Meta
  ($0.65/1k results, gated by B2C/DTC classification) + TikTok (gated by
  Gen-Z/lifestyle classification). Free tier $5/mo covers a single
  8-account batch.

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

### ✅ Phase 1 — Tavily + jobspy only ($0.10-0.30/account)
Modules: **1, 3, 5, 6, 7 (minus new-leader), 9 (jobspy only), 12, 13, 14.**
Shipped. Live-verified on Kali's 8 Priority-A accounts on 2026-05-09.

### ✅ Phase 2 — Add Apify ad libraries + pain-point synthesis
- **2a**: module 10 (LinkedIn always; Meta/TikTok gated by audience).
- **2b**: module 4 — strategic narrative + Pain Point Tags (v2.0.0 →
  v2.2.0 = narrative-output style, no cold-email voice, no module-name
  citations, no horoscope filler).
- **2c** (not originally planned, added 2026-05-11): module 14 v1.5.0 —
  Greenhouse ATS direct read + important-role open/close detection +
  location-aware hiring signal (UK+EU+NA only triggers the `hiring`
  Buying Signal).

All three shipped 2026-05-11.

### ✅ Phase 2.5 — Tiered cadences + Notion-native alerting (2026-05-13)
- **Phase A**: per-module freshness gating via `--module-since` CLI flag.
  Each task can be skipped on a per-account basis when its last successful
  output is within a caller-supplied threshold. Skipped tasks replay their
  cached `output_json` into the context envelope + re-render the page-body
  blocks (no body wipe on daily runs).
- **Phase B**: per-task `detect_events(prev, curr)` hook on Task base. Each
  module owns its own diff rule (Module 06 structure_note severity +
  buying_implication transitions; Module 07 trigger_details URL/summary
  diff; Module 14 important_roles_recently_closed + new Tier-1 opens;
  Module 05 structure_type change; Module 04 timing-shift pain tags).
- **Phase C**: Notion-native alerting. New `Needs Attention` multi_select
  (agent appends, human clears) + new `Attention Acknowledged At` date
  (human-set, clears the 14-day dedup cooldown). One descriptive comment
  per run summarising all events. `event_alerts` SQLite ledger does the
  signature-based dedup.
- **Phase D**: GHA workflows at `.github/workflows/` — daily-news,
  weekly-pulse, monthly-full, quarterly-deep. Staggered cron times in
  Kali's local timezone. Only monthly-full writes `Last Researched`.

### Phase 3 — Hardening for team handoff (pending)
- Cost ceilings per account (`--max-cost`).
- Resume on partial failure (per-task, not just per-account).
- Real Gemini provider (currently a stub).
- ~~Monthly scheduled run (cron or `/schedule` skill).~~ Shipped 2026-05-13
  as Phase 2.5/D — see `.github/workflows/*.yml`.
- Lever / Ashby / Workday ATS providers (Greenhouse-only today).
- Optional: enrich `research_pass` with a "strategic positioning + growth
  direction" section if module 4 narratives feel thin on smaller-profile
  accounts (deferred — re-evaluate after the next live batch).

### Scheduler setup (Track 1 — GHA)

GHA secrets to configure when handing off:
- `ANTHROPIC_API_KEY` — primary LLM provider
- `OPENAI_API_KEY` — for `--provider openai` (cross-provider eval today;
  optional for production)
- `NOTION_API_KEY` — Notion CRM writes
- `TAVILY_API_KEY` — news + general web search
- `APIFY_TOKEN` — LinkedIn/Meta/TikTok ad library scrapers
- `LIBSQL_URL` — Turso libSQL database URL (Phase E persistence)
- `LIBSQL_AUTH_TOKEN` — Turso libSQL auth token

### Database persistence (Phase E, 2026-05-13)

GHA cron runs are stateless — each workflow start gets a fresh filesystem,
which means a local `runs.db` would be empty every cron tick and break:
- **Phase A freshness gating** (no prior rows → nothing is ever "fresh")
- **Phase B diff detection** (no prior_output → events never fire)
- **Phase C alert dedup** (event_alerts ledger starts empty every run)

Solution: when `LIBSQL_URL` is set in the environment, `run_log._open_conn`
routes to a Turso/libSQL remote DB instead of a local SQLite file. Same SQL,
same schema, same migrations — just hosted state.

**Local dev**: leave `LIBSQL_URL` unset and the agent uses local `runs.db`
unchanged.

**Production / GHA**: set both env vars (or repo secrets) and every write
goes to Turso.

**Turso setup** (one-time):
```bash
brew install tursodatabase/tap/turso
turso auth signup            # uses GitHub login
turso db create arr-runs-db  # creates a free-tier hosted database
turso db show arr-runs-db --url     # → libsql://arr-runs-db-XXXX.turso.io
turso db tokens create arr-runs-db  # → eyJhbG... (long-lived token)
```

Add both values to `.env` for local testing and to GHA repo secrets for
production. The Turso free tier (9 GB / 1 B reads / 25 M writes per month)
is comfortably above our workload — 213 accounts × 12 tasks × ~30 runs/month
≈ 75K writes/month.

**Migrating an existing local runs.db to Turso** (only if you want history
carried over; otherwise start fresh and let Phase A/B priors accumulate):
```bash
sqlite3 runs.db .dump > /tmp/runs-dump.sql
turso db shell arr-runs-db < /tmp/runs-dump.sql
```

Cron schedule (UTC, staggered to avoid Notion rate-limit overlap):
- `daily-news.yml`         — `0 5 * * *`         (~06:00 Belgrade)
- `weekly-pulse.yml`       — `30 6 * * MON`      (~07:30 Belgrade, Mon)
- `monthly-full.yml`       — `0 6 1 * *`         (~07:00 Belgrade, 1st)
- `quarterly-deep.yml`     — `0 7 1 1,4,7,10 *`  (~08:00 Belgrade, Q-start)

Track 2 (personal Claude Max version) can run these locally via the
`/schedule` skill — same task lists, same `--module-since` flags, same
ack-clears-cooldown semantics.

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

1. **Salesforce size band mismatch** — Notion has `1000-2000`, Salesforce
   doesn't. Deferred — sync mapping problem, not relevant to research.
2. **Closed in 2026-05-11 work:**
   - ✅ Apify account created; key in `.env`.
   - ✅ Schema additions: `Pain Point Tags`, `out_of_scope` option live.
   - ✅ Buying Signals / Buying Intent role swap.
   - ✅ `Name of Parent` → `Parent` rename.

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

## Cost tracking (2026-05-12)

Every batch prints a cost block at the end:

```
--- Cost (this batch) ---
  Total: $0.0682 (LLM $0.0442 + Tavily $0.0240) across 1 account(s), 1 task-run(s)
  Per account avg: $0.0682  (input: 10,734  output: 800  cached-input: 0  searches: 3)
  Apify (LinkedIn/Meta/TikTok ad scrapers) not included — check dashboard.apify.com.
  Forecast at this per-account rate: 50 accts ~$3.41, 213 ~$14.53, 1255 ~$85.59
```

Add `--cost-summary` for verbose per-account / per-task / per-model breakdown
tables. The batch total is filtered to rows started AFTER batch_started_at,
so it isolates this run from lifetime totals.

For post-hoc analysis, use `cost_report.py`:

```
python cost_report.py                          # everything in runs.db
python cost_report.py --since 7d               # last 7 days (rel offset)
python cost_report.py --since 2026-05-01       # since ISO date
python cost_report.py --account Oracle         # one account
python cost_report.py --git-sha c6b91ba        # one build
python cost_report.py --top 20                 # top N per breakdown
python cost_report.py --json                   # machine-readable
```

Pricing tables live in `config.MODEL_PRICING_PER_M_TOKENS` (Anthropic +
OpenAI + Gemini per-model rates) and `config.TAVILY_COST_PER_SEARCH` (flat
$0.008). Unknown models return $0 — the report flags this case so a stale
runs.db with retired model names doesn't silently under-report.

**Apify is NOT in the report** — it doesn't pass through runs.db. Check
dashboard.apify.com for LinkedIn/Meta/TikTok scraper spend.

## Pointers

- **Run dry:** `python account_research_agent.py --limit 1 --dry-run`
- **Run live:** `python account_research_agent.py --limit 1`
- **Run batch (skip recently researched):** `python account_research_agent.py --since 30`
- **Verbose cost breakdown:** `python account_research_agent.py --cost-summary`
- **Post-hoc cost report:** `python cost_report.py --since 7d`
- **Inspect last run output:**
  `python -c "import sqlite3,json; c=sqlite3.connect('runs.db'); r=c.execute('select output_json from task_runs order by id desc limit 1').fetchone(); print(json.dumps(json.loads(r[0]), indent=2))"`
- **Notion All Accounts DB ID:** `6d510b5a-9c8f-490f-8600-429184341edc`
- **Money Moguls CRM parent page:** `3523435e-7148-8111-b9be-df6e2c5844b9`
