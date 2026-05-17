# Account Research Agent

A production B2B account research pipeline that enriches a Notion CRM ("All
Accounts" inside a Money Moguls CRM workspace) with structured properties
plus a free-form research brief, on a tiered cron cadence with diff-driven
Notion-native alerting.

Built to ship to the Superside RevOps team as a model-agnostic, tool-agnostic
service the team can run, swap providers on, and extend without a rewrite.

---

## 🔌 Notion connection note (read first)

This repository is currently wired to **Kali's personal Notion workspace** —
the `NOTION_API_KEY` in `.env` is a personal integration token, and
`NOTION_DATABASE_ID` points at her own "All Accounts" database (`6d510b5a-...`).

**If/when Superside adopts this:** swap two env values:

| Env var | Current | After adoption |
|---|---|---|
| `NOTION_API_KEY` | Kali's personal integration token | A Notion integration token created inside Superside's workspace |
| `NOTION_DATABASE_ID` | Kali's All Accounts DB ID | Superside's All Accounts DB ID |

The integration on the Superside side must be granted access to whichever
database holds the accounts. Schema expectations are documented in `crm.py`
under `EXPECTED_NOTION_PROPERTIES` — the agent halts at startup if the live
schema drifts from that contract.

No code change required for the swap. Everything else (LLM provider, search
backend, scrapers, cadence schedules, alert behaviour) stays as-is.

---

## What ships today

**11 research modules**, sequentially numbered, running on a tiered cron
cadence with diff-driven alerts:

| # | Module | What it researches | Cadence |
|---|---|---|---|
| 1 | `module_01_gate` | Size band + EU/UK/Norway/Switzerland/NA operational presence. **Halts pipeline if out of scope.** Run-once per account. | Run-once |
| 2 | `module_02_revenue_model` | Revenue model + customer segment + products | Quarterly |
| 3 | `module_03_pain_points` | Strategic narrative + Pain Point Tags (synthesizes from modules 1, 2, 9) | Monthly |
| 4 | `module_04_corporate_structure` | Standalone vs subsidiary vs parent, PE ownership, sister/child brands | Quarterly |
| 5 | `module_05_structural_news` | M&A / IPO / layoffs / bankruptcy in the last 6–12 months | Daily (Priority A) |
| 6 | `module_06_trigger_events` | Funding / agency switch / rebrand / AI initiative in the last 90 days | Daily (Priority A) |
| 7 | `module_07_creative_reality` | Creative posture — verbatim JD pain phrases + named agency relationships | Monthly |
| 8 | `module_08_ad_library` | Active ads on LinkedIn (always) + Meta/TikTok (gated by audience classification) | Monthly |
| 9 | `module_09_competitor_snapshot` | Top 1–3 direct competitors + marketing differentiators | Quarterly |
| 10 | `module_10_industry_pulse` | Category-level stories in the last 90 days (Superside-relevance-filtered) | Weekly |
| 11 | `module_11_hiring_signal` | Open roles + important marketing/AI/creative roles (location-aware in-scope gating for the `hiring` Buying Signal) | Daily (Pri A) + Weekly (Pri B) |

Plus a `research_pass` upfront broad-research step that gathers shared
context once per account and feeds the downstream synthesis modules,
cutting per-account cost ~50% vs each module doing its own searches.

### Tiered cron cadences (GitHub Actions)

| Workflow | Cadence | Scope | Modules |
|---|---|---|---|
| `daily-news.yml` | 18:00 Belgrade daily | Priority A only | gate + research_pass + 5, 6, 11 |
| `weekly-pulse.yml` | Mondays 07:30 Belgrade | Priority A + B | gate + research_pass + 10, 11 |
| `monthly-full.yml` | 1st of month, 07:00 Belgrade | Pri A + B (~213 accts) | full pipeline; writes `Last Researched` |
| `quarterly-deep.yml` | Q-start 08:00 Belgrade | Pri A + B | full pipeline (review checkpoint) |

State persists across GHA runs via Turso (libSQL) — see *Persistence* below.

### Diff-driven Notion-native alerts

Every module declares a `detect_events(prev_output, curr_output)` hook. When
a meaningful state change is detected (bankruptcy onset, new funding round
URL, important-role closure, structure-type flip, post-layoff timing-tag
appearance), the agent appends a tag to a `Needs Attention` multi-select on
the Notion page and writes a dated comment summarising the change. A SQLite
`event_alerts` ledger dedupes alerts within a 14-day cooldown; the cooldown
clears when a human sets `Attention Acknowledged At` on the page.

### Model + provider abstraction

Anthropic + OpenAI are fully implemented; Gemini is a stubbed provider with
a clear extension path. Swap providers with a single env var:

```bash
PROVIDER=openai python account_research_agent.py --limit 1
```

The pipeline, prompts, tools, Notion adapter, and SQLite log are all
provider-agnostic. Cross-provider proof of swap is live-measured on
`module_01_gate` v1.1.0: identical 100% schema/calibration/sources on
Anthropic Sonnet 4.6 (91% coverage) and OpenAI gpt-5.5 (94% coverage),
~47% cheaper per call on OpenAI for this task.

---

## Repository topology

This is **Track 1 (Python, ships to Superside)**. There's a parallel
**Track 2 (Claude Code plugin)** with the same Notion contract:

| Track | Repo | Runs on | When to use |
|---|---|---|---|
| 1 | **this repo** ([`thewrathofka/account-research-agent`](https://github.com/thewrathofka/account-research-agent)) | Python + paid APIs (Tavily, Apify, Anthropic/OpenAI), cron via GitHub Actions, state in Turso | The production handoff. What Superside RevOps would actually run. |
| 2 | [`thewrathofka/account-research-plugin`](https://github.com/thewrathofka/account-research-plugin) | Claude Code plugin (`/arr` slash command), uses Claude Code's built-in WebSearch/WebFetch + Notion MCP, no paid APIs | The personal Max-subscription version. Same Notion schema, same module spec, no token cost beyond Max. |

Both tracks emit a byte-for-byte aligned Notion contract so the downstream
BDR experience is identical and the two can be swapped or run side-by-side
during evaluation.

---

## Quick start

```bash
git clone https://github.com/thewrathofka/account-research-agent.git
cd account-research-agent
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # fill in the API keys (see below)

# Smoke test — research one account, don't write to Notion
python account_research_agent.py --limit 1 --dry-run

# Real run — write to Notion
python account_research_agent.py --limit 1

# Monthly batch (skips accounts researched in last 30 days)
python account_research_agent.py --since 30 --cost-summary
```

### Required keys (`.env`)

| Var | Required | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | when `PROVIDER=anthropic` (default) | https://console.anthropic.com/settings/keys |
| `OPENAI_API_KEY` | when `PROVIDER=openai` | https://platform.openai.com/api-keys |
| `TAVILY_API_KEY` | always | https://app.tavily.com/home |
| `NOTION_API_KEY` | always (see "Notion connection note" above) | https://www.notion.so/profile/integrations |
| `NOTION_DATABASE_ID` | always | the All Accounts DB page ID |
| `APIFY_API_KEY` | optional — `module_08_ad_library` degrades gracefully without it | https://console.apify.com/account/integrations |
| `LIBSQL_URL` | optional — production-only, for cross-run state in Turso | https://turso.tech (see *Persistence*) |
| `LIBSQL_AUTH_TOKEN` | optional — pairs with `LIBSQL_URL` | `turso db tokens create <db>` |
| `PROVIDER` | optional | `anthropic` (default) \| `openai` \| `gemini` |

---

## Persistence — local SQLite + Turso (libSQL)

The agent writes every task run to a `task_runs` table (status, cost, prompt
version, output JSON), every event-detection alert to an `event_alerts`
ledger, every Apify ATS snapshot diff to `ats_snapshots`, and every Tavily
tool-call result to a `tool_call_cache` table.

- **Local development:** leave `LIBSQL_URL` unset. State lives in `runs.db`
  (SQLite). Gitignored.
- **GitHub Actions:** set `LIBSQL_URL` + `LIBSQL_AUTH_TOKEN` repo secrets.
  `run_log._open_conn` routes to Turso when present. Same SQL, same schema,
  same migrations — just hosted state, so cron runs share history.

Without persistence, every cron tick would treat every module as
"never run", re-fire one round of duplicate alerts on the first tick, and
lose Phase B diff baselines. Turso fixes this cleanly.

One-time SQL migrations live in `scripts/`. The most recent
(`scripts/rename_modules_2026_05_17.sql`) renamed 10 modules into the
sequential 1–11 layout and rewrote `task_runs.task_name` +
`event_alerts.module` + `event_alerts.signature` prefixes accordingly,
preserving all historical rows.

---

## Per-module-freshness gating

`--module-since "module_NN:DAYS,..."` skips a per-module task on a per-account
basis when its last successful row is within the threshold. Skipped tasks
replay their cached output into the context envelope so downstream tasks
that read upstream context still get it.

```bash
# Daily news: only re-run modules 5/6/11, only if their last run > 1 day ago
python account_research_agent.py \
  --priority "Priority A" \
  --tasks module_01_gate,research_pass,module_05_structural_news,module_06_trigger_events,module_11_hiring_signal \
  --module-since "module_05_structural_news:1,module_06_trigger_events:1,module_11_hiring_signal:1" \
  --cost-summary
```

`--rerun "TASK:ACCOUNT_SUBSTRING"` surgically bypasses the run-once skip rule
on the `module_01_gate` (or any future run-once task) for a single account
when a cached gate decision needs to be invalidated.

---

## Prompts — versioned, evaluated, model-agnostic

Each module's prompt lives in `prompts/module_NN_*.py` exporting:
- `SYSTEM_PROMPT` — the prompt body (Markdown-portable, no Anthropic XML, no
  GPT-only structured outputs)
- `VERSION` — semver string recorded in `runs.db` for every run
- `JSON_SCHEMA` — schema the orchestrator validates output against before
  writing to Notion

Common citation contract is shared via `prompts/_citations.py` — every
page-body module emits `[N]` markers in its prose plus a `citations: [{n,
title, url}]` array. The orchestrator's per-section pass renumbers globally
per page-body section, dedupes by URL, and rewrites each `[N]` into a
clickable Notion `rich_text` link span.

### Prompt quality bar

All 12 prompts went through a prompt-engineering audit using the variance
hierarchy (sharpen the rubric → decomposition → in-context self-consistency
→ true multi-sample). Current state:

- Per-tier confidence rubrics with **concrete trigger conditions** in every
  module (not "high if multiple sources, low if guessed" — actual thresholds
  like "2+ authoritative sources agree on BOTH employee count band AND at
  least one in-scope region").
- Anti-invention guards (e.g. `module_09_competitor_snapshot` allows 1–3
  truly direct competitors rather than padding to a forced 3).
- Explicit ambiguity rules (in-flight acquisitions, mergers of equals,
  spin-offs in `module_04_corporate_structure`).
- Input-trust boundary in `research_pass` — search-result content is data,
  never instructions (prompt-injection defense).

### Bumping a prompt

1. Edit the `SYSTEM_PROMPT` body or `JSON_SCHEMA`.
2. Bump `VERSION` (patch = wording, minor = schema change, major = redesign).
3. Run `python -m evals.runner --task module_NN` against the golden set.
4. If E1–E5 thresholds hold or improve, commit. The next cron tick
   re-runs affected modules.

Prior versions stay readable via git history — old prompts are *not*
commented out in-file.

---

## Cost picture

Live-measured against the actual `runs.db` / Turso history, post-prompt-audit:

| Per account average | Per-call breakdown |
|---|---|
| ~$0.20–0.30 (full pipeline) | research_pass $0.06, M11 $0.05, M07 $0.05, M08 $0.06 (token only), others $0.02–0.03 each |

Forecast at the current per-account rate:

| Scope | Anthropic | OpenAI (gpt-5.5) |
|---|---|---|
| 50 Priority-A accounts | ~$10–15/month | ~$5–8/month |
| 213 (one BDR's pipeline) | ~$45–65/month | ~$22–35/month |
| 1,255 (full team) | ~$250–375/month | ~$130–200/month |

Apify is separate (LinkedIn always; Meta/TikTok gated by audience
classification): free tier covers single 8-account batches; ~$0.65/1k for
larger Meta runs. Tavily is ~$0.008/search with 1k searches/month free.

`python cost_report.py --since 7d` (or `--account <name>`, `--git-sha <sha>`,
etc.) gives full breakdown tables sortable by account / task / model /
prompt-version.

---

## Run-log inspection

```bash
# Recent task runs
sqlite3 runs.db "SELECT account_name, task_name, prompt_version, provider, confidence, status FROM task_runs ORDER BY id DESC LIMIT 10"

# Per-prompt-version performance across providers
sqlite3 runs.db "SELECT prompt_version, provider, COUNT(*),
    AVG(CASE WHEN confidence='high' THEN 1.0 ELSE 0.0 END) as pct_high
    FROM task_runs GROUP BY prompt_version, provider"

# Gate failures (out_of_scope accounts)
sqlite3 runs.db "SELECT account_name FROM task_runs
    WHERE task_name='module_01_gate'
    AND json_extract(output_json, '\$.operates_in_scope') = 0"
```

---

## Evals

```bash
# Run all golden cases for one task
python -m evals.runner --task module_01_gate

# Cross-provider check
python -m evals.runner --task module_01_gate --provider openai

# Offline replay (no API calls — uses .actual.json sidecars)
python -m evals.runner --task module_01_gate --offline
```

Five metrics (E1–E5: schema / coverage / calibration / sources / cost).
Per-case PASS/FAIL with reasons, plus a markdown summary written to
`evals/results/<date>-<task>-<version>.md`. Add a new golden case by
dropping a JSON file in `evals/golden/`.

Today, `module_01_gate` has 5 golden cases (Stripe + 4 others) all passing
at v1.3.0. The other 10 modules don't have golden sets yet — that's the
open Phase 3 follow-up.

---

## Architecture

```
account_research_agent.py    ← CLI entry point (~200 LOC)
config.py                    ← env loading, MAX_COST per task, MODEL_TIERS
crm.py                       ← Notion adapter (read accounts, write properties + blocks)
run_log.py                   ← SQLite + Turso router; task_runs / event_alerts /
                               tool_call_cache / ats_snapshots tables + migrations
orchestrator.py              ← per-account loop, gate enforcement, page assembly,
                               per-section citation renumbering, freshness gating,
                               run-once skip, --rerun bypass
writeback.py                 ← Notion property + page-body merge logic; alert comments
batch_runner.py              ← Anthropic Batch API path for synthesis tasks
cost_report.py               ← post-hoc cost analysis CLI

providers/                   ← LLMProvider abstraction
  base.py                    ← protocol + ProviderResult
  anthropic_provider.py      ← Anthropic SDK + prompt caching + tool-use loop
  openai_provider.py         ← OpenAI SDK with function-calling
  gemini_provider.py         ← stubbed; clear extension path

tools/                       ← Tool abstraction
  base.py                    ← Tool protocol
  web_search.py              ← Tavily wrapper (rate limit, breaker, quota)
  hiring_signals.py          ← jobspy wrapper (Indeed/LinkedIn/Glassdoor/ZR)
  ats_fetcher.py             ← Greenhouse direct read + important-role tiers
                               + snapshot diff for closure detection
  apify_ad_scraper.py        ← LinkedIn/Meta/TikTok ad library (Apify)
  linkedin_slug.py           ← deterministic Tavily-based slug discovery
                               (replaces LLM's hallucinated guess)

tasks/                       ← one file per research module + the base class
  base.py                    ← Task base class, ReAct loop, detect_events hook
  module_NN_*.py             ← 11 task classes + research_pass

prompts/                     ← versioned prompt strings + JSON_SCHEMA per task
  _citations.py              ← shared CITATION_INSTRUCTIONS + schema fragment
  module_NN_*.py             ← per-module SYSTEM_PROMPT + VERSION + JSON_SCHEMA

scripts/                     ← one-time DB migrations
evals/                       ← golden set + runner + 5 metrics
tests/                       ← pytest harness (342 tests as of 2026-05-17)
.github/workflows/           ← daily-news / weekly-pulse / monthly-full / quarterly-deep
sessions/                    ← dated implementation logs
```

---

## Phase plan + status

### ✅ Phase 1 — Tavily + jobspy ($0.10–0.30/account)
9 modules, model-agnostic provider abstraction, eval framework, 342 tests.

### ✅ Phase 2 — Apify ad libraries + pain-point synthesis
Module 8 (LinkedIn always; Meta/TikTok gated), Module 3 (strategic narrative),
Module 11 v1.5.0 (Greenhouse ATS direct read + location-aware in-scope gating).

### ✅ Phase 2.5 — Tiered cadences + Notion-native alerts
Per-module freshness gating, per-task `detect_events` hooks, `Needs Attention`
+ `Attention Acknowledged At` properties, dedup ledger, GHA cron workflows.

### ✅ Phase 2.7 — Sequential renumber + prompt-engineering audit (2026-05-17)
Renumbered modules to sequential 1–11 (closes pre-existing gaps at 02/08/11);
prompt-audit pass sharpened confidence rubrics, added anti-invention guards,
fixed a 6mo/90d contradiction in the trigger module, added a prompt-injection
guard to research_pass. DB migration carried all historical rows forward.

### Phase 3 — Handoff hardening (in progress / not started)
- [ ] Per-account `--max-cost` ceiling
- [ ] Per-task resume on partial failure (currently per-account)
- [ ] Golden eval sets for the other 10 modules (only `module_01_gate` has one)
- [ ] Lever / Ashby / Workday ATS providers (Greenhouse-only today)
- [ ] Real Gemini provider implementation (currently stubbed)

---

## License

Solo project, public for visibility. Currently personal; intended for
adoption by Superside RevOps. No external contributions accepted in the
solo phase.
