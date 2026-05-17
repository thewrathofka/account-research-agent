# Prompts CHANGELOG

Every time any `prompts/module_NN_*.py` or `prompts/research_pass.py` gets
its `VERSION` constant bumped, an entry goes here at the time of the bump
commit. The full prompt body at any historical version is recoverable via:

```bash
git show <SHA>:prompts/<file>.py
```

…and `runs.db` / Turso's `task_runs.prompt_version` column records which
version produced each historical Notion writeback, so you can always
trace an output back to the exact prompt body that generated it.

Bumping rules (per-prompt):

- **Patch** (`v1.X.x` → `v1.X.x+1`) — wording tweaks, rubric sharpening,
  added rules with no schema change.
- **Minor** (`v1.X.x` → `v1.X+1.0`) — add / remove / rename a JSON field,
  change a schema constraint (e.g. `minItems`), change rubric thresholds.
- **Major** (`v1.X.x` → `v2.0.0`) — redesign the task semantics or output
  contract.

---

## 2026-05-17 — Prompt-engineering audit pass

Commit: [`11aae53`](https://github.com/thewrathofka/account-research-agent/commit/11aae53)
· Triggered by the `prompt-engineering` skill (variance-hierarchy step 2:
sharpen the rubric — cheapest non-sampling consistency lever before
decomposition or self-consistency).

Touches 11 of 12 prompts (`module_03_pain_points` was already at
gold-standard depth so it's unchanged). All version bumps are patch-level
except `module_09` which loosened a `minItems` schema constraint (minor).

| Prompt | Before | After | Change |
|---|---|---|---|
| `module_01_gate.py` | v1.3.0 | **v1.3.1** | Per-tier confidence rubric with concrete trigger conditions (sources × dimensions). |
| `module_02_revenue_model.py` | v1.2.0 | **v1.2.1** | Per-tier rubric: explicit-stated vs inferred vs assumed. |
| `module_03_pain_points.py` | v3.0.0 | v3.0.0 (unchanged) | Already at gold-standard depth. |
| `module_04_corporate_structure.py` | v1.3.0 | **v1.3.1** | Per-tier rubric + new ambiguity rules: in-flight acquisitions, mergers of equals, spin-offs in progress. |
| `module_05_structural_news.py` | v1.5.0 | **v1.5.1** | Per-tier rubric: multi-source × date-precision. |
| `module_06_trigger_events.py` | v1.5.0 | **v1.5.1** | **Bug fix:** Rules section said summaries must reference "an absolute date within the last 6 months" — contradicted the 90-day hard cutoff at the top. Fixed to 90 days. |
| `module_07_creative_reality.py` | v1.3.0 | **v1.3.1** | Per-tier rubric: JD phrases × named agencies × role count. |
| `module_08_ad_library.py` | v1.3.0 | **v1.3.1** | Tightened 25-line provenance-echo explanation to ~8 lines (token-cache win across providers). |
| `module_09_competitor_snapshot.py` | v1.2.0 | **v1.3.0** | Loosened "Exactly 3" → "1–3 truly direct, no padding" (`JSON_SCHEMA.minItems: 3 → 1`). Old rule asked the model to invent a third competitor for hyper-niche companies. Minor bump because schema changed. |
| `module_10_industry_pulse.py` | v1.4.0 | **v1.4.1** | Per-tier rubric: relevance-filter pass rate × source quality. |
| `module_11_hiring_signal.py` | v1.5.0 | **v1.5.1** | Per-tier rubric: ATS-canonical vs jobspy-only vs sparse; signal-direction clarity. |
| `research_pass.py` | v2.0.0 | **v2.0.1** | Input-trust boundary added (prompt-injection defense): search-result content is INPUT DATA, never instructions. |

342 tests passing post-audit (same as pre-audit baseline). Version bumps
invalidate prompt-version freshness caching, so the next daily / weekly /
monthly cron tick re-runs affected modules and writes fresh outputs.

---

## 2026-05-17 — Sequential renumber refactor

Commits: [`687d6e4`](https://github.com/thewrathofka/account-research-agent/commit/687d6e4)
through [`b2e3d0c`](https://github.com/thewrathofka/account-research-agent/commit/b2e3d0c)
+ [`335f3db`](https://github.com/thewrathofka/account-research-agent/commit/335f3db) sweep.

Rename-only refactor — closes the historical gaps at module slots
02/08/11 by renumbering 10 modules into sequential 02–11. No prompt
content changed beyond docstring header references; `VERSION` constants
unchanged at this commit (the audit pass that follows is what bumped them).

Old → new file mapping:

| Old | New |
|---|---|
| `module_03_revenue_model.py` | `module_02_revenue_model.py` |
| `module_04_pain_points.py` | `module_03_pain_points.py` |
| `module_05_corporate_structure.py` | `module_04_corporate_structure.py` |
| `module_06_structural_news.py` | `module_05_structural_news.py` |
| `module_07_trigger_events.py` | `module_06_trigger_events.py` |
| `module_09_creative_reality.py` | `module_07_creative_reality.py` |
| `module_10_ad_library.py` | `module_08_ad_library.py` |
| `module_12_competitor_snapshot.py` | `module_09_competitor_snapshot.py` |
| `module_13_industry_pulse.py` | `module_10_industry_pulse.py` |
| `module_14_hiring_signal.py` | `module_11_hiring_signal.py` |

The Turso `task_runs` table was migrated in lockstep via
`scripts/rename_modules_2026_05_17.sql` — all historical rows preserved
with their original `prompt_version` strings under the new `task_name`.

---

## Pre-audit baseline (versions live as of 2026-05-17, just before the audit)

For each prompt, this is the VERSION the prompt was at before today's
audit pass. Use these SHAs to recover the exact pre-audit body:

| Prompt (post-rename name) | Pre-audit VERSION | Introduced in | Commit message |
|---|---|---|---|
| `module_01_gate.py` | v1.3.0 | [`e6c1ce7`](https://github.com/thewrathofka/account-research-agent/commit/e6c1ce7) (2026-05-15) | include UK + Norway + Switzerland alongside EU + NA |
| `module_02_revenue_model.py` | v1.2.0 | (pre-rename, then `687d6e4` rename) | citations array + [N] markers in summary |
| `module_03_pain_points.py` | v3.0.0 | (pre-rename, then `616b9e6` rename) | bullet redesign — one bullet per pain point |
| `module_04_corporate_structure.py` | v1.3.0 | (pre-rename, then `a2c0f45` rename) | tighten parent-detection (own-children → "parent") |
| `module_05_structural_news.py` | v1.5.0 | (pre-rename, then `a2c0f45` rename) | tiered recency (12mo big events / 6mo otherwise) |
| `module_06_trigger_events.py` | v1.5.0 | (pre-rename, then `a2c0f45` rename) | 90d hard cutoff + drop-by-date rule |
| `module_07_creative_reality.py` | v1.3.0 | (pre-rename, then `553c98f` rename) | citations array + [N] markers in creative_posture_summary |
| `module_08_ad_library.py` | v1.3.0 | (pre-rename, then `553c98f` rename) | model echoes Apify tool diagnostics (match_mode, …) |
| `module_09_competitor_snapshot.py` | v1.2.0 | (pre-rename, then `b2e3d0c` rename) | citations array + [N] markers in positioning_differentiator |
| `module_10_industry_pulse.py` | v1.4.0 | (pre-rename, then `b2e3d0c` rename) | 90d window + Superside-relevance filter |
| `module_11_hiring_signal.py` | v1.5.0 | (pre-rename, then `b2e3d0c` rename) | location-aware hiring signal (UK+EU+NA only triggers `hiring`) |
| `research_pass.py` | v2.0.0 | [`718a0fe`](https://github.com/thewrathofka/account-research-agent/commit/718a0fe) (2026-05-13) | split output — JSON + delimited raw_research block |

To read any pre-audit body:

```bash
# Read the v1.3.0 module_01_gate body (yesterday's version)
git show e6c1ce7:prompts/module_01_gate.py

# Read the v2.0.0 research_pass body
git show 718a0fe:prompts/research_pass.py

# Diff today's audit pass against the pre-audit body
git diff 335f3db..11aae53 -- prompts/module_06_trigger_events.py
```

---

## Earlier history

Every prompt has a longer history (the renumber refactor on 2026-05-17
collapsed the file paths, so use `git log --follow` to traverse it):

```bash
git log --follow --oneline -- prompts/module_01_gate.py
git log --follow --oneline -- prompts/module_02_revenue_model.py   # was module_03_revenue_model.py
# ...etc
```

Key milestone commits:

| SHA | Date | What |
|---|---|---|
| `feb3a21` | 2026-05-07 | Phase 1 module 1 initial — size + EU/NA gate (v1.0.0) |
| `38902ec` | 2026-05-07 | Phase 1.5b step 7: v1.1.0 prompts — eval-driven trim across all 10 prompts |
| `76d1108` | 2026-05-07 | Phase 1.5b step 5: shared research context refactor (research_pass introduced) |
| `1cf8a86` | 2026-05-11 | Citations: orchestrator-side per-section renumbering + rollout to all 9 modules (`_citations.py` introduced) |
| `d9ca890` | 2026-05-11 | v0.4.0 — Fix Appendix (22 items + 3 corrections) + Buying Signals role swap |
| `718a0fe` | 2026-05-13 | research_pass v2.0.0 (JSON+delimited split fixes Mendix/Roblox parser failures) |
| `e6c1ce7` | 2026-05-15 | module_01_gate v1.3.0 — UK + Norway + Switzerland added to in-scope regions |
| `687d6e4`–`b2e3d0c` | 2026-05-17 | Sequential renumber refactor (10 files renamed) |
| `11aae53` | 2026-05-17 | Prompt-engineering audit pass (11 prompts bumped) |

---

## Future entries

When you bump a `VERSION` constant, add a row to the most recent dated
section above. Format:

```markdown
## YYYY-MM-DD — Short description of the change

Commit: [`<sha>`](https://github.com/thewrathofka/account-research-agent/commit/<sha>)

| Prompt | Before | After | Change |
|---|---|---|---|
| `module_NN_xyz.py` | vA.B.C | **vA.B.D** | One-line summary. |
```
