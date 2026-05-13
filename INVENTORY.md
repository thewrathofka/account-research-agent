# Account Research Agent — Module & Output Inventory

Generated 2026-05-13 for a manual lock-down review. Cross-checked against the
code as-of commit `b65abe9` (chunk 4 of the Phase 2 ship). Trust the code over
this file if they ever diverge.

---

## 1. Pipeline Overview

**Entry point.** `python account_research_agent.py` runs `main()` in
`account_research_agent.py:105`. It:
1. Parses CLI args (`--rep`, `--priority`, `--limit`, `--account`, `--since`,
   `--tasks`, `--dry-run`, `--batch`, `--concurrency`, `--provider`,
   `--cost-summary`, `--label`, `--verbose`) — see `_parse_args` (`:61`).
2. Constructs `NotionCRM()` and calls `crm.validate_schema()`
   (`account_research_agent.py:117`); halts with exit 2 if Notion drifts from
   `EXPECTED_NOTION_PROPERTIES`.
3. Queries accounts via `crm.list_accounts(rep, priority_type, researched_before, limit=None)`
   (`crm.py:150`) — then applies `--account` substring filter and `--limit`
   in-process.
4. Builds `Orchestrator` (or calls `batch_runner.run_batch` if `--batch`).
5. Per account, the orchestrator (`ThreadPoolExecutor`, `--concurrency` default
   5, `config.DEFAULT_CONCURRENCY`) runs each task sequentially in
   `_process_account` (`orchestrator.py:102`), passing a `context: dict[task_name -> output]`
   envelope between tasks.
6. After tasks complete: aggregate confidence (`_aggregate_confidence`, `:237`)
   + derive status (`_derive_status`, `:249`); write to Notion via
   `writeback.write_account_outcome` unless `dry-run` or
   `overall_status == "failed"`.
7. Print Summary + Cost block + lifetime run-log stats.

> ⚠️ **Default `--tasks` value is `company_overview`** (`account_research_agent.py:74`)
> — a legacy placeholder. To run the real Phase 2 pipeline you must pass
> `--tasks <list>` (recommended set is `PHASE2_TASKS` in `tasks/__init__.py:65`).

**Task registry order** (`tasks/__init__.py:25`, dict insertion order):
1. `company_overview` (legacy)
2. `research_pass`
3. `module_01_gate`
4. `module_03_revenue_model`
5. `module_04_pain_points`
6. `module_05_corporate_structure`
7. `module_06_structural_news`
8. `module_07_trigger_events`
9. `module_09_creative_reality`
10. `module_10_ad_library`
11. `module_12_competitor_snapshot`
12. `module_13_industry_pulse`
13. `module_14_hiring_signal`

**`PHASE2_TASKS` execution order** (the recommended live order,
`tasks/__init__.py:65`) — this is what actually runs end-to-end:

```
module_01_gate → research_pass → module_03 → module_05 → module_06 →
module_07 → module_09 → module_10 → module_12 → module_13 → module_14 →
module_04_pain_points
```

Module 04 runs **last** because it synthesizes from `research_pass` +
`module_01` + `module_03` + `module_12` (`tasks/module_04.py:27`,
`ANCHOR_MODULES`).

**Gate halt.** `GATE_TASKS = {"module_01_gate"}` (`tasks/__init__.py:82`). If
`Module01Gate.gate_passes()` returns False (i.e.
`output["operates_in_eu_or_na"]` is False), `_process_account` breaks the loop
and calls `_handle_gate_failure` (`orchestrator.py:157`), which delegates to
`writeback.write_gate_failure_to_notion`.

**Model tiers — TWO tiers exist per provider** (`config.py:63-74`):
- Anthropic: smart = `claude-sonnet-4-6`, fast = `claude-haiku-4-5`
- OpenAI: smart = `gpt-5.5`, fast = `gpt-5.5-mini`
- Gemini: smart = `gemini-2.5-pro`, fast = `gemini-2.5-flash` (stub provider)

Across the 3 providers × 2 tiers there are 6 model configurations; each task's
`model_tier` attribute resolves to whichever provider is active at runtime.

**Per-task tier assignment** (read from `Task.model_tier` class attribute,
default `"smart"` from `tasks/base.py:109`):
- **smart tier**: `research_pass`, `module_01_gate`, `module_09_creative_reality`,
  `module_10_ad_library`, `module_14_hiring_signal` (all tool-using; default
  not overridden).
- **fast tier**: `module_03_revenue_model`, `module_04_pain_points`,
  `module_05_corporate_structure`, `module_06_structural_news`,
  `module_07_trigger_events`, `module_12_competitor_snapshot`,
  `module_13_industry_pulse` (all `synthesis_only = True`; cost-cut on
  2026-05-12).

**Synthesis-only vs tool-using.** `Task.synthesis_only = True` → `max_iter = 1`,
no tools, single LLM call, reads `context["research_pass"]["raw_research"]`
(`tasks/base.py:111` + `:212`).

---

## 2. Notion Schema (Properties Written)

`EXPECTED_NOTION_PROPERTIES` is in `crm.py:76`. The agent halts at startup if
Notion drifts.

| Property | Type | Writer module | Vocabulary (if multi-select) | When written |
|---|---|---|---|---|
| `Account Name` | title | (input only) | — | never (read only) |
| `Size` | select | `module_01_gate` (`tasks/module_01.py:60`) | `<1000`, `1000-2000`, `2000-5000`, `5000+` | every successful gate; bucketed from integer estimate via `size_bucket()` |
| `Buying Signals` | multi_select | `module_07` + `module_13` + `module_14` | `funding round`, `active creative jobs`, `rebrand/campaign`, `agency switch`, `AI initiative`, `industry movement`, `hiring`, `downsizing` (`crm.py:45`) | always present in payload — `writeback.build_property_payload` defaults to empty `multi_select` so stale tags clear on rerun (`writeback.py:144`) |
| `Buying Intent` | multi_select | — | (human-only) | **never written by agent** (2026-05-11 role swap; `crm.py:50`) |
| `Pain Point Tags` | multi_select | `module_04_pain_points` (`tasks/module_04.py:101`) | `creative production`, `localization`, `new territory`, `strategy`, `audience education`, `competitive displacement`, `brand evolution`, `launch surge`, `AI receptivity`, `post-layoff overflow` (`crm.py:57`) | every M4 run (empty list valid → clears stale) |
| `Structure Notes` | rich_text | `module_06_structural_news` (`tasks/module_06.py:20`) | constrained vocab: `recent IPO`, `about to IPO`, `merged with X`, `acquired X`, `acquired by X`, `mass layoffs`, `bankruptcy`, `split from X`, `buying-frozen`, `buying-friendly`, `out of business` | only when `structure_note` is non-null; merge strategy = append with ` · ` separator (`writeback.py:88`) |
| `Parent` | rich_text | `module_05_corporate_structure` (`tasks/module_05.py:22`) | — | written when `structure_type == "subsidiary"` (parent name) OR when company is itself a parent / has notable child brands (writes own `_account_name`); skipped for standalone-no-children |
| `sister/child` | rich_text | `module_05_corporate_structure` | — | written when `notable_sister_or_child_brands` is non-empty (comma-joined) |
| `Last Researched` | date | (agent metadata) | — | written in `build_completion_payload` after page body succeeds (`writeback.py:148`) |
| `Research Confidence` | select | (agent metadata) | `high`, `medium`, `low`, `failed` (downgraded to `low` if `failed`) | written in `build_completion_payload` |
| `Research Status` | select | `module_01_gate` (gate failure → `out_of_scope`) + metadata derivation | `pending`, `done`, `needs_review`, `failed`, `out_of_scope` | written in `build_completion_payload`; merge strategy = `merge_status_priority` (severity wins, `writeback.py:73`) |
| `Company Structure` | rich_text | — (declared in `EXPECTED_NOTION_PROPERTIES` but **no module writes it**) | — | never |

Manual-only properties exist on the Notion DB but are **not** in
`EXPECTED_NOTION_PROPERTIES`: `Rep`, `Priority Type`, `Lead Signal`,
`New Hire`, `Notes`. The agent reads `Rep` + `Priority Type` for filtering but
never writes them.

> ⚠️ Two schema mismatches: `sister/child` is **written** but **not in
> `EXPECTED_NOTION_PROPERTIES`** (silent 400 risk). `Company Structure` is
> **expected** but never **written**.

---

## 3. Page-Body Section Layout

Headings are emitted in order by `_research_section_blocks` in
`orchestrator.py:412`, using `_SECTION_ORDER` (`:271`) and `_SUBSECTION_ORDER`
(`:281`). The actual rendered shape on each Notion page:

1. **heading_2** `Research — YYYY-MM-DD[ — <label>]` (`crm.build_section_heading_text`, `crm.py:105`)
2. **heading_3 `Overview`**
   - `module_01_gate` paragraph (size band · employee count · regions)
   - `module_03_revenue_model` paragraph (summary)
   - `module_05_corporate_structure` paragraph
   - **heading_3 `Overview — Headcount`** → `module_14_hiring_signal` summary + important-roles bullets
3. **heading_3 `Possible Pain Points`** → `module_04_pain_points` (intro paragraph + per-pain bullets + `Pain tags: …` bullet)
4. **heading_3 `News`**
   - `module_06_structural_news` paragraph
   - `module_07_trigger_events` paragraph + per-trigger bullets
   - One nested `heading_3` per detected Buying Signal tag (e.g. `funding round`, `industry movement`, `hiring`, `downsizing`) emitted from `TaskResult.signal_sections` — each with a logic paragraph + `Sources:` paragraph + URL bullets
5. **heading_3 `Creative Posture`** → `module_09_creative_reality` summary + agency bullet + JD-pain-phrase bullets
   - **heading_3 `Creative Posture — Ads Running`** → `module_10_ad_library` per-platform bullets (with `provenance` nested-bullet diagnostics)
6. **heading_3 `Competitor Landscape`** → `module_12_competitor_snapshot` paragraph + per-competitor bullets, then `module_13_industry_pulse` headline bullets
7. **divider**

Inline `[N]` citations are clickable links via `_rewrite_block_citation_markers`
(`orchestrator.py:333`). Per-section "Sources:" footnote bullets + global
page-bottom "Sources" heading_3 were both removed on 2026-05-12 (v3.0.0) — see
comments at `orchestrator.py:445` and `:526`. Per-signal subsections under
News still carry their own URL bullets (they're evidence dumps, not citations).

---

## 4. Per-Module Catalog

### `research_pass` (v2.0.0)
- **Purpose:** one broad upfront research call per account; populates the shared context envelope (`raw_research` prose + canonical platform identifiers) so synthesis-only modules don't re-search.
- **Tools:** `web_search` (Tavily).
- **Model tier:** smart (Sonnet).
- **Inputs:** none.
- **Output JSON top-level keys (required):** `company_name`, `raw_research`, `sources`, `confidence`, `company_website`, `linkedin_company_url`, `facebook_page_url`, `tiktok_handle`, `greenhouse_slug`.
- **Writes to Notion:** none.
- **Page body:** none.
- **Quirks:** `raw_research` is injected by `post_parse` from a `<<<RAW_RESEARCH>>> … <<<END_RAW_RESEARCH>>>` delimited block (`tasks/research_pass.py:25`), keeping bulky markdown out of the JSON to avoid string-escape parser failures (Mendix #706, Roblox #557, 2026-05-12). All five canonical-ID fields are nullable; downstream tools fall back to free-text search when null. Guessed URLs are explicitly forbidden.

### `module_01_gate` (v1.2.0)
- **Purpose:** size + EU/NA gate. Halts pipeline if neither EU nor NA presence.
- **Tools:** `web_search`.
- **Model tier:** smart.
- **Inputs:** none.
- **Output:** `company_name`, `employee_count_estimate`, `operates_in_eu`, `operates_in_na`, `operates_in_eu_or_na`, `regions_present`, `evidence_eu?`, `evidence_na?`, `sources`, `confidence`, `reason_if_out_of_scope?`.
- **Writes to Notion (properties):** `Size` only, bucketed deterministically from `employee_count_estimate` via `size_bucket()` (`tasks/module_01.py:33`).
- **Page body:** Overview paragraph — `Size band: <bucket> · ~<N> employees · Operations in EU + NA`. On gate failure → `Out of scope: <reason>`.
- **Quirks:** there is intentionally NO `Employee Count` number property — the integer lives in `output_json` only.

### `module_03_revenue_model` (v1.2.0)
- **Purpose:** revenue model + customer segment + primary products as a 2-3 sentence Overview paragraph.
- **Tools:** none (synthesis-only).
- **Model tier:** fast (Haiku).
- **Inputs:** `research_pass.raw_research` + `sources`.
- **Output:** `revenue_model`, `primary_customer_segment`, `primary_products`, `summary`, `citations`, `sources`, `confidence`.
- **Writes to Notion:** none.
- **Page body:** one paragraph under Overview, with `[N]` citation markers.
- **Quirks:** if private + unclear → `Unclear from public sources` + `confidence="low"`.

### `module_05_corporate_structure` (v1.3.0)
- **Purpose:** classify standalone vs subsidiary vs parent; capture PE owner + sister/child brands.
- **Tools:** none (synthesis-only).
- **Model tier:** fast.
- **Inputs:** `research_pass`.
- **Output:** `structure_type` (enum: `standalone|subsidiary|parent`), `parent_company?`, `is_pe_owned`, `pe_owner?`, `notable_sister_or_child_brands`, `citations`, `sources`, `confidence`.
- **Writes to Notion (properties):**
  - `Parent` = parent name if subsidiary; else company's own `_account_name` if `structure_type == "parent"` OR `notable_sister_or_child_brands` is non-empty (self-name guard for AlphaSense-like cases); else not written.
  - `sister/child` = comma-joined sibling/child brands when non-empty.
- **Page body:** one Overview paragraph (`Corporate structure: <type>. PE owner: <X>. Notable brands: <list>.`).
- **Quirks:** prompt v1.3.0 enforces that any non-empty `notable_sister_or_child_brands` forces `structure_type="parent"` (fields cannot disagree).

### `module_06_structural_news` (v1.5.0)
- **Purpose:** last-6/12mo structural events (M&A, IPO, layoffs, bankruptcy) → constrained-vocab `Structure Notes` property + News paragraph.
- **Tools:** none (synthesis-only).
- **Model tier:** fast.
- **Inputs:** `research_pass`.
- **Output:** `structure_note?`, `event_date?`, `event_summary?`, `buying_implication?` (enum: `buying-frozen|buying-friendly|null`), `citations`, `sources`, `confidence`.
- **Writes to Notion (properties):** `Structure Notes` = `structure_note` text (only when non-null). Merge strategy = `merge_rich_text_append` (`writeback.py:112`), so reruns concatenate with ` · ` separator.
- **Page body:** News paragraph `<Title>: <summary> (<date>) — <implication>`.
- **Quirks:** v1.4.0+ hardens placeholder substitution (must write actual counterparty name); v1.5.0 adds tiered recency (12mo for big events, 6mo otherwise) and absolute-date YYYY-MM-DD requirement.

### `module_07_trigger_events` (v1.5.0)
- **Purpose:** detect trigger events in last 90 days; tag `Buying Signals` + render News bullets + signal subsections.
- **Tools:** none (synthesis-only).
- **Model tier:** fast.
- **Inputs:** `research_pass`.
- **Output:** `triggers_detected` (subset of: `funding round`, `active creative jobs`, `rebrand/campaign`, `agency switch`, `AI initiative`), `trigger_details[{trigger, summary, url}]`, `citations`, `sources`, `confidence`.
- **Writes to Notion (properties):** `Buying Signals` multi-select with the detected triggers, filtered against `BUYING_SIGNAL_OPTIONS`.
- **Page body:** "Buying signals (last 90 days):" paragraph + one bullet per trigger.
- **Signal sections:** one entry per trigger via `to_signal_sections` (`tasks/module_07.py:54`).
- **Quirks:** "active creative jobs" only fires on a press release / announcement, not routine listings (those are M14's job).

### `module_09_creative_reality` (v1.3.0)
- **Purpose:** mine active JDs for creative-pain phrases + named agency partners.
- **Tools:** `hiring_signals` (jobspy), `web_search` (Tavily).
- **Model tier:** smart.
- **Inputs:** none injected directly; module runs its own agent loop.
- **Output:** `creative_role_count`, `jd_pain_phrases`, `named_agencies`, `creative_posture_summary`, `citations`, `sources`, `confidence`.
- **Writes to Notion:** none.
- **Page body:** Creative Posture paragraph (summary) + bullet `Named agency partners: …` + `Pain phrases from active JDs:` paragraph + one bullet per phrase.
- **Quirks:** `named_agencies` must be press-confirmed or company-claimed only.

### `module_10_ad_library` (v1.3.0)
- **Purpose:** ad-library presence on LinkedIn (always), Meta (gated by B2C/DTC classification), TikTok (gated by Gen-Z/lifestyle classification).
- **Tools:** `apify_ad_scraper`.
- **Model tier:** smart.
- **Inputs:** `research_pass` (raw_research excerpt + canonical URLs), `module_03_revenue_model.primary_customer_segment`, `module_09_creative_reality.named_agencies`.
- **Output:** `audience_classification{primary (B2B|B2C|hybrid), is_gen_z_lifestyle, rationale}`, `platforms[{platform (linkedin|meta|tiktok), ads_running, volume (none|low|medium|high), format_mix?, note, provenance?{match_mode, filtered_out, url_boosted}}]` (exactly 3 items), `citations`, `sources`, `confidence`.
- **Writes to Notion:** none.
- **Page body:** under "Creative Posture — Ads Running" subsection, one bullet per platform: `<platform>: <count> ads (<volume> volume) — <format_mix>. <note>` + nested provenance child bullet `match_mode: X · filtered_out: N · url_boosted: N`.
- **Quirks:** v1.3.0 prompt requires the model to echo Apify tool diagnostics through verbatim. v5 of the Apify tool: canonical URL is a *filter booster* (URL-matched items bypass advertiser-name Jaccard threshold of 0.5), not a search override.

### `module_12_competitor_snapshot` (v1.2.0)
- **Purpose:** top 3 direct competitors + positioning differentiator each.
- **Tools:** none (synthesis-only).
- **Model tier:** fast.
- **Inputs:** `research_pass`.
- **Output:** `competitors[{name, positioning_differentiator}]` (exactly 3), `citations`, `sources`, `confidence`.
- **Writes to Notion:** none.
- **Page body:** Competitor Landscape paragraph `Top direct competitors: …` + one bullet per competitor.
- **Quirks:** if fewer than 3 clear competitors, prompt instructs fill with closest matches + `confidence="low"`.

### `module_13_industry_pulse` (v1.4.0)
- **Purpose:** 2-3 last-90d category-level stories; may trigger `industry movement` Buying Signal.
- **Tools:** none (synthesis-only).
- **Model tier:** fast.
- **Inputs:** `research_pass`.
- **Output:** `industry`, `stories[{headline, url, buying_implication?}]` (0-3), `industry_movement_detected`, `citations`, `sources`, `confidence`.
- **Writes to Notion (properties):** `Buying Signals` adds `industry movement` (only) when `industry_movement_detected == true`.
- **Page body:** Competitor Landscape paragraph `Industry pulse — recent <industry> stories:` + one bullet per story headline.
- **Signal sections:** when triggered, one entry `{signal: "industry movement", logic, sources}`.
- **Quirks:** v1.4.0 expanded window 60d→90d and tightened relevance filter.

### `module_14_hiring_signal` (v1.5.0)
- **Purpose:** hiring/downsizing classification with location-aware in-scope counting + important-role open/close diff.
- **Tools:** `hiring_signals` (jobspy), `ats_jobs` (Greenhouse), `web_search` (Tavily, layoff news only).
- **Model tier:** smart.
- **Inputs:** `module_01_gate` (regions_present, employee_count_estimate), `module_06_structural_news` (layoff event context), `research_pass` (greenhouse_slug).
- **Output:** `active_open_roles_total`, `creative_marketing_roles_count`, `creative_marketing_roles_in_scope_count`, `creative_marketing_role_titles?`, `recent_layoffs_detected`, `layoff_summary?`, `headcount_signal?` (enum: `hiring|downsizing|null`), `headcount_summary`, `important_roles_open?[{title, tier, url?, location?, in_scope?}]`, `important_roles_recently_closed?[{title, tier}]`, `citations`, `sources`, `confidence`. `tier` enum: `tier_1_marketing_ai`, `tier_2_senior_leadership`, `tier_3_senior_creative_ic`.
- **Writes to Notion (properties):** `Buying Signals` adds `hiring` or `downsizing` (single value) when `headcount_signal` is set.
- **Page body:** under "Overview — Headcount" subsection: summary paragraph + `Important roles currently open (N — X in UK/EU/NA, Y elsewhere, Z unknown):` paragraph + up to 10 role bullets + `Important roles closed since previous research run (N) — recent closure may indicate the team is now in place...:` paragraph + closed-role bullets.
- **Quirks:** the `hiring` Buying Signal triggers iff `creative_marketing_roles_in_scope_count >= 3` (UK + EU + NA only). Out-of-scope creative hiring appears in headcount page body for context but doesn't fire the signal. ATS snapshot diff is persisted in `runs.db` via `ATSSnapshotStore`. Greenhouse-only today.

### `module_04_pain_points` (v3.0.0)
- **Purpose:** strategic analyst's-brief — intro line + 2-5 named pain bullets + 2-5 tags.
- **Tools:** none (synthesis-only).
- **Model tier:** fast.
- **Inputs:** `research_pass.raw_research` + `ANCHOR_MODULES = ["module_01_gate", "module_03_revenue_model", "module_12_competitor_snapshot"]`. **Deliberately NOT given** the mechanical-signal modules (06/07/09/10/14) — those have their own page-body sections + signal subsections.
- **Output:** `intro` (≥10 chars), `pain_points[{label, body}]` (1-5), `tags` (subset of `PAIN_POINT_TAG_OPTIONS`, 0-5), `citations`, `sources`, `confidence`.
- **Writes to Notion (properties):** `Pain Point Tags` multi-select with validated tags (empty multi_select clears stale tags).
- **Page body:** "Possible Pain Points" section: intro paragraph + one bullet per pain (`<label> — <body>`) + `Pain tags: tag1 · tag2 · …` bullet.
- **Quirks:** v3.0.0 (2026-05-12) replaces v2.x narrative paragraphs with bullets for 10-second scannability. v2.x cached outputs with a `narrative` field still render as paragraphs (backwards-compat path at `tasks/module_04.py:136`).

---

## 5. Tools

| Tool | What it does | API / Backend | Cost | Returns |
|---|---|---|---|---|
| `web_search` (Tavily) | Web search with `query` + optional `days` recency; per-task cap 8 (`TAVILY_SEARCHES_PER_TASK_CAP`); 24h success cache, 30min transient-error cache; process-wide HTTP 432 (quota exhausted) circuit breaker | Tavily | $0.008/search, 1k/mo free | Title/URL/Content per result (max 5) |
| `web_search` (Brave) | Same name + contract as Tavily, drop-in via `SEARCH_BACKEND=brave` | Brave Search API | variable | Title/URL/description |
| `read_url` | Fetch URL → clean Markdown, 24h cache; built when `SEARCH_BACKEND=brave_jina` | Jina Reader (`r.jina.ai`) | free tier 1M tokens/mo + 200 RPM | Full-page Markdown |
| `hiring_signals` | Aggregate Indeed/LinkedIn/Glassdoor/ZipRecruiter via jobspy; normalize company-name match (strips Inc/LLC/GmbH); takes `countries` list | python-jobspy | free | Titles + per-country counts |
| `ats_jobs` | Greenhouse public API direct read; three-tier important-title regex; SQLite snapshot persistence; `is_in_scope_location()` classifier (UK+EU+NA = in-scope) | `boards-api.greenhouse.io/v1/boards` | free | Total roles + per-location + important_roles + recently_closed_important_roles |
| `apify_ad_scraper` | One platform per call; canonical URL/handle as filter booster (URL-matched items bypass Jaccard 0.5); per-task cap 3 calls, 50 max results; 7-day success cache | Apify actors | Variable; not tracked in runs.db | Per-platform: ads_running, volume, format_mix, provenance, sample URLs |

---

## 6. Cost Picture (current)

From `config.py:120`:

```
claude-sonnet-4-6 : $3.00 / M input, $15.00 / M output, $0.30 / M cached_input
claude-haiku-4-5  : $1.00 / M input,  $5.00 / M output, $0.10 / M cached_input
gpt-5.5           : $2.00 / M input,  $8.00 / M output, $0.50 / M cached_input
gpt-5.5-mini      : $0.40 / M input,  $1.60 / M output, $0.10 / M cached_input
gemini-2.5-pro    : $3.50 / M input, $10.50 / M output, $0.875 / M cached_input
gemini-2.5-flash  : $0.30 / M input,  $2.50 / M output, $0.075 / M cached_input
```

- **Tavily:** `$0.008/search` flat (`config.TAVILY_COST_PER_SEARCH`), 1k/mo free.
- **Per-task budgets** (`config.MAX_COST`): module_01_gate $0.04, research_pass $0.06, module_03 $0.02, module_05 $0.02, module_06 $0.02, module_07 $0.03, module_09 $0.05, module_10 $0.06 (token only, Apify separate), module_12 $0.02, module_13 $0.02, module_14 $0.05, module_04 $0.04, `_account_total` ceiling $0.40.
- **Eval cost-regression threshold:** `COST_REGRESSION_OVERAGE = 0.20` (20% headroom).
- **CLAUDE.md reports** per-account average $0.20–0.30 at v0.4.0 + Phase 2.

**Forecasts at $0.25/account:** 213 accts → ~$53.25; 1,255 accts → ~$313.75.
**Forecasts at $0.20/account:** 213 accts → ~$42.60; 1,255 accts → ~$251.00.

**Not tracked in `runs.db`:**
- **Apify spend** — `tool_results_seen` can't distinguish free LinkedIn from paid Meta. CLI prints "Apify (LinkedIn/Meta/TikTok ad scrapers) not included — check dashboard.apify.com" (`account_research_agent.py:231`).
- **jobspy / Greenhouse / Jina** — free, no per-call cost recorded.

---

## 7. Known Limitations & Phase 3 Carryover

**Still open:**
- **Phase 3 hardening:** cost ceilings per account flag (`--max-cost` CLI surface absent; `_account_total` budget exists in `config.MAX_COST` but is eval-only, not runtime-enforced); per-task resume; monthly scheduled cron; Lever / Ashby / Workday ATS providers.
- **Real Gemini provider** — stubbed.
- **OpenAI Batch API** — Anthropic Batch shipped, OpenAI Batch not.
- **Cross-provider eval coverage** — only `module_01_gate` has been cross-provider tested.
- **Per-prompt golden cases** — modules 3, 4, 5, 6, 7, 9, 10, 12, 13, 14 lack hand-annotated goldens.
- **Strategic positioning enrichment in `research_pass`** — deferred.

**Listed in CLAUDE.md as open but actually shipped:**
- `Pain Point Tags` populated.
- `Name of Parent` → `Parent` rename.
- `Parent-Child` → `sister/child` rename.
- `Buying Signals` / `Buying Intent` role swap.
- Tavily quota circuit breaker.
- ATS direct read with snapshot diff + location-aware hiring signal.
- Per-section citation renumbering + clickable `[N]` link spans.
- Module 4 v3.0.0 bullet layout.
- Global page-bottom "Sources" + per-section footnote bullets — both removed.

**Quirks worth spot-checking** (most fixed in 2026-05-13 lockdown; see `AUDIT.md`):
- ~~Default `--tasks` is `company_overview`.~~ Fixed: default is now `PHASE2_TASKS`.
- ~~CLAUDE.md mentions a "5-tier model".~~ My error — CLAUDE.md only says "model tiers" (plural). Two tiers exist.
- ~~`Company Structure` declared but no module writes it.~~ Fixed: removed from `EXPECTED_NOTION_PROPERTIES`.
- ~~`sister/child` written but not in `EXPECTED_NOTION_PROPERTIES`.~~ Fixed: added.
- `--label` heading replacement matches only sections with the same label, so multiple variants coexist on a page.
- `CompanyOverview` is still in `TASK_REGISTRY` for legacy test fixtures. Don't pass `company_overview` to a live run.
