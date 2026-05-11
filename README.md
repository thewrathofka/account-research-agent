# Account Research Agent

A monthly batch agent that researches B2B accounts in a Notion CRM ("All Accounts"
inside Money Moguls CRM) and writes structured findings + a free-form research
summary back to each account's Notion page.

Built model-agnostic and tool-agnostic so the LLM provider (Anthropic / OpenAI /
Gemini) and search backend can be swapped without rewriting the pipeline.

## Quick start

```bash
git clone <this-repo>
cd account-research-agent
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # then fill in the API keys (see below)

# Smoke test — research one account, don't write to Notion
python account_research_agent.py --limit 1 --dry-run

# Real run — write to Notion
python account_research_agent.py --limit 1

# Monthly batch (skips accounts researched in last 30 days)
python account_research_agent.py --since 30 \
  --tasks module_01_gate,module_03_revenue_model,module_05_corporate_structure,module_06_structural_news,module_07_trigger_events,module_09_creative_reality,module_12_competitor_snapshot,module_13_industry_pulse,module_14_hiring_signal
```

## Required keys (`.env`)

| Var | Required when | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | active provider is Anthropic (default) | https://console.anthropic.com/settings/keys |
| `OPENAI_API_KEY` | active provider is OpenAI | https://platform.openai.com/api-keys |
| `TAVILY_API_KEY` | always | https://app.tavily.com/home |
| `NOTION_API_KEY` | always | https://www.notion.so/profile/integrations |
| `NOTION_DATABASE_ID` | always | the All Accounts DB ID |
| `APIFY_API_KEY` | optional — required only for `module_10_ad_library` | https://console.apify.com/account/integrations |
| `PROVIDER` | optional | `anthropic` (default) \| `openai` \| `gemini` |

If `APIFY_API_KEY` is absent, module 10 degrades gracefully: it returns
`ads_running=0` per platform with `confidence="low"` and a "not configured"
note, so the rest of the pipeline keeps shipping.

## Swapping LLM providers

Two ways:

**One-shot via env var:**
```bash
PROVIDER=openai python account_research_agent.py --limit 1
```

**Permanent via `.env`:**
```env
PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Or override per-invocation:
```bash
python account_research_agent.py --provider openai --limit 1
```

That is the entire swap surface. The pipeline, prompts, tools, Notion adapter,
SQLite log — none of them know which provider is active. This is the
"company-handoff" promise of the abstraction.

`gemini` is currently a stub (raises `NotImplementedError` with a clear path to
implement). To fill it in, follow the pattern in `providers/anthropic_provider.py`
or `providers/openai_provider.py` and translate to Gemini's `functionCall` /
`functionResponse` format.

## Phase 1 modules

| Task name | What it researches | Output |
|---|---|---|
| `module_01_gate` | Size + EU/NA presence (GATE) | `Size`; if no EU/NA → `Research Status=out_of_scope`, halts other modules |
| `module_03_revenue_model` | Revenue model + customer segment + products | Page-body Overview |
| `module_05_corporate_structure` | Standalone vs subsidiary vs parent + PE | `Name of Parent`, `Parent-Child` |
| `module_06_structural_news` | M&A / IPO / layoffs in last 6 months | `Structure Notes` |
| `module_07_trigger_events` | Funding / rebrand / agency switch / AI initiatives (90d) | `Buying Intent` multi-select |
| `module_09_creative_reality` | Creative posture (in-house signals + agency) | Page-body Creative Posture |
| `module_12_competitor_snapshot` | Top 3 direct competitors | Page-body Competitor Landscape |
| `module_13_industry_pulse` | Recent category-level stories | Adds to Competitor Landscape; `Buying Intent: industry movement` |
| `module_14_hiring_signal` | Active hiring (creative/marketing) or layoffs | `Buying Signals: hiring` or `downsizing`; Headcount sub-section |
| `module_10_ad_library` *(Phase 2a)* | Ads running on LinkedIn (always) + Meta / TikTok (gated by audience) | Page-body `Creative Posture → Ads Running` bullets, per-platform count + format mix + volume |
| `module_04_pain_points` *(Phase 2b)* | Synthesizes 2-4 grounded pain hypotheses from modules 1, 3, 7, 9, 10, 14 outputs | Page-body `Possible Pain Points` bullets, each pain mapped to one of 6 Superside value-prop angles with cited grounding data |

Page-body sections are always assembled in this order:
**Overview → Possible Pain Points → News → Creative Posture → Competitor Landscape → Sources**

## Running evals

```bash
# Run all golden cases for one task
python -m evals.runner --task module_01_gate

# Run all Phase 1 tasks
python -m evals.runner --all

# Use OpenAI for the eval run
python -m evals.runner --task module_01_gate --provider openai

# Replay last live run without API calls (uses .actual.json sidecars)
python -m evals.runner --task module_01_gate --offline
```

Eval output: per-case PASS/FAIL with reasons, aggregate metrics vs thresholds
(plan §7.1: schema≥95%, coverage≥80%, calibration≥90%, sources≥98%), and a
markdown summary written to `evals/results/<date>-<task>-<version>.md`.

To add a new golden case: drop a JSON file in `evals/golden/` keyed by company
slug. Format documented at the top of `evals/runner.py`.

## Updating prompts

Each task's prompt lives in `prompts/module_NN_*.py` with an explicit `VERSION`
constant. The version is recorded in `runs.db` for every task run so you can
diff prompt impact over time.

When you change a prompt:
1. Bump `VERSION` (patch for wording, minor for schema change, major for redesign).
2. Run `python -m evals.runner --task module_NN` against the golden set.
3. If E1–E4 thresholds hold or improve, commit.
4. If they regress, refine and re-run before merging.

Prior versions stay readable via git history — old prompts are *not* commented
out in-file.

## Architecture

```
account_research_agent.py    ← thin CLI entry point
config.py                    ← env loading + tunables (PROVIDER_NAME etc.)
crm.py                       ← Notion adapter (read accounts, write properties + blocks)
run_log.py                   ← SQLite per-run log (cost, prompt_version, git_sha)
orchestrator.py              ← per-account loop, gate enforcement, page assembly
providers/                   ← LLMProvider abstraction
  base.py                    ← protocol + ProviderResult
  anthropic_provider.py      ← Anthropic SDK wrapper
  openai_provider.py         ← OpenAI SDK wrapper
  gemini_provider.py         ← stub
tools/                       ← Tool abstraction
  base.py                    ← Tool protocol
  web_search.py              ← Tavily wrapper
  hiring_signals.py          ← jobspy wrapper (Indeed/LinkedIn/Glassdoor/ZipRecruiter)
tasks/                       ← one file per research question
  base.py                    ← Task base class + ReAct loop driver
  module_NN_*.py             ← concrete tasks
prompts/                     ← versioned prompt strings + JSON_SCHEMA per task
evals/                       ← golden set + runner + metrics
tests/                       ← pytest harness (24 tests as of v0.1.0)
```

## Run-log inspection

The SQLite run log (`runs.db` by default) records every task call:

```bash
sqlite3 runs.db "SELECT account_name, task_name, prompt_version, provider, confidence, status FROM task_runs ORDER BY id DESC LIMIT 10"
```

Useful queries:

```bash
# How much did the last batch cost?
sqlite3 runs.db "SELECT SUM(input_tokens), SUM(output_tokens), SUM(search_count) FROM task_runs WHERE started_at > date('now', '-1 day')"

# Which prompts performed how across providers?
sqlite3 runs.db "SELECT prompt_version, provider, COUNT(*), AVG(CASE WHEN confidence='high' THEN 1.0 ELSE 0.0 END) as pct_high FROM task_runs GROUP BY prompt_version, provider"

# Which accounts failed the gate?
sqlite3 runs.db "SELECT account_name, output_json FROM task_runs WHERE task_name='module_01_gate' AND json_extract(output_json, '\$.operates_in_eu_or_na') = 0"
```

## Cost picture

Tools-only (excluding LLM tokens):

| Scope | Phase 1 monthly |
|---|---|
| 8 Priority-A accounts | $1–3 |
| 213 (one BDR's pipeline) | $25–80 |
| 1,255 (full team) | $150–500 |

LLM tokens add roughly $0.10–0.30/account on Anthropic Sonnet 4.5; ~$0.05–0.15
on OpenAI gpt-4.1.

## Phase 2

- ✅ Module 10 (ad library — LinkedIn always; Meta/TikTok gated by audience classification) — shipped 2026-05-11
- ✅ Apify integration with cost gates + graceful degrade (`APIFY_API_KEY` optional) — shipped 2026-05-11
- ✅ Module 4 (pain-point synthesis from modules 1, 3, 7, 9, 10, 14 outputs) — shipped 2026-05-11
- Cross-provider eval comparison run on the full pipeline

## Phase 3 (handoff hardening)

- Cost ceiling per account (`--max-cost`)
- Per-task resume on partial failure
- Monthly scheduled run via cron
- Gemini provider implementation
