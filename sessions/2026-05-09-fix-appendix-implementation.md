# 2026-05-09 — Fix Appendix Implementation Session

> **Numbering note (2026-05-17):** This session log references the
> pre-rename module numbering (modules 3–14 with gaps). Modules were
> renumbered sequentially 1–11 on 2026-05-17. Old → new mapping: 3→2, 4→3,
> 5→4, 6→5, 7→6, 9→7, 10→8, 12→9, 13→10, 14→11. See INVENTORY.md for the
> current numbering.

A working log of everything implemented in one session against the
`booklet_fix_appendix.pdf` recommendations, three follow-up corrections
discovered while live-testing, and an open bug to ship next time.

---

## Session inputs

- **Source doc:** `~/Documents/Codex/2026-05-07/files-mentioned-by-the-user-booklet/booklet_fix_appendix.pdf`
  — 22 numbered recommendations covering correctness, idempotency, cost
  control, source trust, Notion hygiene, and provider portability.
- **Project:** `~/code/account-research-agent/` (Track 1, Python, v0.3.0).
- **Provider during the run:** `claude-sonnet-4-6` (Anthropic).

## What shipped

### All 22 Fix Appendix items (one PR's worth)

| # | Title | Files touched |
|---|---|---|
| 1 | Propagate batch outputs into Pass 3 context | `batch_runner.py` |
| 2 | Idempotent Notion page appends (`replace_latest_research_section`) | `crm.py`, `writeback.py` |
| 3 | Don't mark Last Researched until page body write succeeds | `writeback.py`, `orchestrator.py`, `batch_runner.py` |
| 4 | Out-of-scope accounts in batch mode | `batch_runner.py`, `writeback.py` |
| 5 | Bracket-balanced JSON extraction | `tasks/base.py` |
| 6 | Verify sources against observed tool results | `run_log.py`, `tasks/base.py`, `evals/metrics.py`, `evals/runner.py` |
| 7 | SQLite WAL + busy_timeout for threaded writes | `run_log.py` |
| 8 | Versioned tool-call cache key (backend + tool_version) | `tools/web_search.py`, `tools/brave_search.py`, `tools/jina_reader.py` |
| 9 | Don't cache transient (429/5xx/network) failures | same as #8 + `run_log.py:ToolCallCache.store(ttl_minutes=...)` |
| 10 | Hard caps return tool error pre-call | search tools |
| 11 | Reject overlong queries (>350 chars) | search tools |
| 12 | Region-aware hiring search | `prompts/module_01_gate.py`, `prompts/module_14_hiring_signal.py`, `tasks/module_14.py`, `tools/hiring_signals.py` |
| 13 | Replace substring company matching with normalize+fuzzy | `tools/hiring_signals.py` |
| 14 | Per-service rate limiters | new `rate_limit.py`, wired into CRM + Anthropic + OpenAI + tools |
| 15 | Shared writeback module | new `writeback.py`; orchestrator + batch_runner delegate |
| 16 | Explicit `MERGE_STRATEGIES` per property type | `writeback.py` |
| 17 | More golden eval coverage | new `evals/golden/{roblox,alphasense,stripe,oracle,india_only}.module_*.json` |
| 18 | Penalize chronic under-confidence | `evals/metrics.py:score_underconfidence` |
| 19 | Cost regression budgets | `config.py:MAX_COST + estimate_usd_cost`, runner gate |
| 20 | Notion property naming + schema check | `crm.py:EXPECTED_NOTION_PROPERTIES + validate_schema` |
| 21 | Size: integer first, bucket second | `prompts/module_01_gate.py`, `tasks/module_01.py:size_bucket` |
| 22 | Provider portability: real OpenAI batch + real Gemini | `providers/{openai_provider.py,gemini_provider.py,base.py}` |

**Net diff at PR-end:** ~1,200 insertions / ~280 deletions across 23 files; 2 new modules (`writeback.py`, `rate_limit.py`); 5 new golden cases; 24/24 pytest tests passing; ruff clean.

### Three follow-up corrections (post-live-test)

After the 22 items were in, three issues surfaced and were fixed in the same session:

**A. Model versions out of date.** `OPENAI_MODELS["smart"]` was `gpt-4.1` and `ANTHROPIC_MODELS["smart"]` was `claude-sonnet-4-5`. Bumped to `gpt-5.5` / `gpt-5.5-mini` and `claude-sonnet-4-6`. `MODEL_PRICING_PER_M_TOKENS` mapped to the new IDs (placeholder pricing carried forward; flagged in comment to refresh once final numbers ship).

**B. News recency was leaking 2024 and 2025 stories.** The `last 6 months` instruction in module 6 / 7 / 13 prompts was being interpreted relative to the model's training cutoff, not "today".

Fix:
- Added a `days` parameter to `web_search` (`tools/web_search.py`) that passes `topic="news" + days` to Tavily so the recency window is enforced at the API layer. Cache key bumped to `web_search_v3`.
- `tasks/base.py:_today_header()` prepends `Today is YYYY-MM-DD.` to every task's user message — synthesis + tool-using tasks both anchor "recent" to a real absolute date.
- `prompts/research_pass.py` v1.2.0: news/event searches use `days=90` first, fall back to `days=180` only on empty result; raw_research must tag every news item with an absolute YYYY-MM-DD; anything older than 6 months is dropped.
- `prompts/module_06_structural_news.py` v1.2.0: explicit 0–3 month preferred / 3–6 month fallback / >6 month hard cutoff; `event_date` required and verified.
- `prompts/module_07_trigger_events.py` v1.2.0: 90/180-day tiering; every summary must reference an absolute date.
- `prompts/module_13_industry_pulse.py` v1.2.0: 60-day preferred / 90-day hard cap; dateless candidates dropped.

**C. The `Employee Count` Notion property addition was wrong.** Fix #21 had me add `Employee Count: number` to `EXPECTED_NOTION_PROPERTIES` and write the integer to a new Notion column. Kali clarified the Money Moguls CRM only has the `Size` select — adding a new column wasn't the simplification she wanted.

Fix: removed `Employee Count` from `EXPECTED_NOTION_PROPERTIES` and dropped the field write from `Module01Gate.to_fields`. The integer-output → `size_bucket()` → `Size`-select flow stays exactly the same; the integer still lives in the run log's `output_json` for audit, but it is **not** a Notion column.

### Live-run results (replace all Priority-A research)

8 Priority-A accounts assigned to Katarina, run live with the full Phase 1 task list under Sonnet 4.6. Idempotent rewrite verified — every account's prior `Research — YYYY-MM-DD` block was archived, then the new section appended; no duplicates.

| Account | Status | Confidence | Tasks ok |
|---|---|---|---|
| Oracle | done | medium | 10/10 |
| PAR Technology | done | medium | 10/10 |
| Roblox | needs_review | low | 10/10 |
| AlphaSense | needs_review | low | 10/10 |
| Moody's | needs_review | low | 10/10 |
| WIRED | needs_review | low | 10/10 |
| Mendix | needs_review | low | 10/10 |
| Miro | needs_review | low | 10/10 |

Six `needs_review / low` outcomes, two `done / medium`. Higher hedging rate than expected — most likely the new tightened recency rules dropping items the model can't date-verify (working as designed but threshold may be too strict), plus JobSpy Glassdoor + ZipRecruiter both 403'ing throughout the run so module 14 ran on Indeed + LinkedIn only.

## Open issue (carried forward)

**Buying-signal staleness across runs** — surfaced on AlphaSense.

The new run correctly outputs `headcount_signal: null` (no recent layoffs in the last 6 months — the November 2024 Tegus-related event is now correctly filtered out). With null `headcount_signal`, `Module14HiringSignal.to_fields` returns `{}`. That means **module 14 doesn't include `Buying Signals` in the Notion patch at all this run**. Since Notion's `pages.update` is partial, the previous run's `"downsizing"` tag stays on the page — contradicting the new page body, which says "no layoffs detected, suggesting stable headcount."

Same shape applies to `Buying Intent` (Module 7's `funding round`, `agency switch`, `AI initiative`, etc.). Stale agent-set tags accumulate.

A naive overwrite is wrong because both properties are **dual-purpose**: the human owns `MQA`, `unify high`, `sales nav mod`, etc., and the agent owns `hiring`/`downsizing`/etc.

**Fix to ship:** reconcile-with-existing on each write.
1. Define `AGENT_MANAGED_BUYING_SIGNALS = {"hiring", "downsizing"}` and `AGENT_MANAGED_BUYING_INTENT = {"funding round", "active creative jobs", "rebrand/campaign", "agency switch", "AI initiative", "industry movement"}` in `crm.py`.
2. Add `NotionCRM.get_page_properties(page_id)` (one extra GET per account).
3. In `writeback.write_account_outcome`, before composing pre_body_props: fetch existing values for each agent-managed multi_select, drop the agent-managed-but-not-in-new-write tags, keep user-managed tags, union with new agent tags.
4. Re-run the 8 Priority-A accounts so `Buying Signals` reconciles with the actual research bodies. AlphaSense should end up with whatever human-set tags were there but **without** `downsizing`.

## How to rebuild the booklet PDF

```
cd ~/Documents/Codex/2026-05-07/files-mentioned-by-the-user-booklet/
python make_fix_appendix.py
```

Regenerates `booklet_fix_appendix.pdf` from `make_fix_appendix.py`. The 2026-05-09 update marks each item with implementation status, adds the three follow-up corrections, and calls out the open issue.
