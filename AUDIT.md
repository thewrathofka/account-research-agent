# Account Research Agent — Pre-Lockdown Code Audit

Generated 2026-05-13 against commit `b65abe9`. Findings are prioritized by
whether they could cause real production damage at 213-account scale.

Tests are 267/267 green. This audit is about what the tests *don't* catch.

---

## CRITICAL (must fix before scaling)

### C1. `validate_schema()` doesn't check `sister/child`

`crm.py:76` (`EXPECTED_NOTION_PROPERTIES`) is missing the `sister/child`
property even though Module 5 actively writes to it (`tasks/module_05.py:46`).

If the property is renamed or removed in Notion, `validate_schema()` returns
clean at startup and every Module 5 writeback silently 400s mid-run,
scattering "wrong" failure rows across the batch.

**Fix:** Add `"sister/child": "rich_text"` to `EXPECTED_NOTION_PROPERTIES`.
This is exactly the failure mode Fix Appendix #20 (`validate_schema`) was
built to prevent.

### C2. JSON Schema `enum` arrays contain Python `None` not JSON `null`

`prompts/module_06_structural_news.py:119`, `module_13_industry_pulse.py:106`,
`module_14_hiring_signal.py:151`. E.g.:

```python
"enum": ["hiring", "downsizing", None]
```

These are valid Python dicts but produce ambiguous JSON Schemas. If any tooling
ever uses `jsonschema.Draft7Validator(JSON_SCHEMA)` directly on the in-memory
dict, validation will misbehave. The `type` union (`["string", "null"]`)
already permits null — drop `None` from `enum`.

### C3. Cost-summary's "Tavily quota" counter undercounts massively

`run_log.py:283-286` does substring matching on `err.message` to count
quota-degraded rows. But after the breaker trips, every downstream task error
is the short-circuit string `"ERROR: Tavily monthly quota exhausted (HTTP 432)"`
— and those rows are NOT `status='failed'`. The LLM still returned
`end_turn`, just with `confidence=low`.

So `failed_tavily_quota` undercounts massively. Either count from
`output_json` (model said `confidence=low` AND saw the 432 string) or surface
a separate "quota-degraded successes" line.

### C4. `_current_git_sha()` silently returns None outside the repo dir

`orchestrator.py:550-558` runs `git rev-parse HEAD` once at Orchestrator init,
but doesn't pin CWD. When running as a cron from a different working
directory, `subprocess.DEVNULL` swallows stderr and `FileNotFoundError` is
caught — silent `git_sha=None` in every row.

Consequence: `cost_report.py --git-sha c6b91ba` (a documented feature in
CLAUDE.md §535) silently returns no rows.

**Fix:** `cwd=Path(__file__).parent` in the subprocess call, or fail loudly.

---

## IMPORTANT (fix in this session)

### I1. Confidence `"failed"` silently downgraded to `"low"`

`tasks/base.py:266-267`: `if confidence not in {"high", "medium", "low"}: confidence = "low"`.

If a model self-reports `confidence: "failed"` (e.g. it couldn't do the
work), the orchestrator treats it as "low" → status becomes `needs_review` →
Notion gets written. "Wrong data is worse than missing data" — better to set
`confidence="failed"` and short-circuit the write. Same pattern in
`batch_runner.py:327-328`.

### I2. `PROP_PAIN_POINT_TAGS` not in `MERGE_STRATEGIES`

`writeback.py:109-114`. Module 4 is the sole writer today so the default
`replace_value` behaviour happens to be correct. But the explicit default for
*all* agent-only multi-selects should be `merge_multi_select` (compare with
the explicit `PROP_BUYING_SIGNALS` entry at `:144`).

Add `crm_module.PROP_PAIN_POINT_TAGS: merge_multi_select` for symmetry.

### I3. `cached_input_tokens` is provider-leaky

`providers/anthropic_provider.py:80` reads `cache_read_input_tokens` which is
Anthropic-only. OpenAI's `prompt_tokens_details.cached_tokens` represents
auto-cache hits with completely different semantics (>1024 token threshold,
no developer control).

Cost numbers across providers aren't comparable as-is. Document this in
`MODEL_PRICING_PER_M_TOKENS` so the OpenAI "cached_input" rate of $0.50 isn't
applied to auto-cache reads — the cost is real, but the BDR/Kali might think
the system saw 50% cache hits when reality is 5%.

### I4. Cached "No results for: <query>" strings can bake false negatives

`tools/web_search.py:262-275`: when `results` is empty, the literal string
`f"No results for: {query}"` is cached for 24h.

The model can interpret this as a real null result and emit
`confidence=high`. At 213-account scale this could lock in false negatives
across a whole batch. Consider a shorter TTL (30 min) for empty results, or
distinguish "verified absent" from "bad query → empty."

### I5. `ApifyApiError` import shadow

`tools/apify_ad_scraper.py:32-34`: when the apify-client package isn't
installed, `ApifyApiError = Exception` (catch-all). Every Apify error then
goes through the retry-store path with a 30-min TTL.

Mostly fine, but worth a comment or restructuring so the failure mode is
explicit.

### I6. `batch_runner` doesn't preserve user-supplied task order

`batch_runner.py:79-86`: the user may pass `--tasks module_03,module_01_gate`
but the batch runner reorders implicitly via three buckets. Sync orchestrator
preserves user order — divergence between paths is exactly the Fix Appendix #4
class of bug.

Preserve ordering inside each bucket, and warn if the user-supplied order
doesn't match the bucket order.

### I7. Synthesis-only "general knowledge" fallback can fabricate

`tasks/base.py:171-178`: when `research_pass` didn't run, the prompt tells the
model "Return your best-effort answer based on general knowledge". But
synthesis-only tasks have `tools=[]` and `max_iter=1` — the model is being
asked to fabricate from training data, with no source verification.

The instruction says "set confidence='low' and leave fields null where you
cannot verify" but the model often doesn't comply. Better: short-circuit
synthesis-only with `confidence='failed'` and `output=None` when `research_pass`
output is empty, so writeback is gated.

### I8. Inconsistent "empty array OK" rules across modules

Module 7 says return [] and confidence "medium" or "low"; Module 13 says
return empty list; Module 14 has different wording. Tighten to one rule and
re-templated guidance.

---

## NICE-TO-HAVE (defer or leave alone)

### N1. Dead code: `company_overview` legacy placeholder

`tasks/__init__.py:25-39` + `tasks/_legacy.py` + `prompts/_legacy.py`.
`CompanyOverview` is the default `--tasks` value but isn't in `PHASE1_TASKS` /
`PHASE2_TASKS` and was last touched on 2026-05-07. Remove the registration +
legacy files (keep the `account_research_agent.py` default off too — set it
to fail loudly if `--tasks` is not provided).

### N2. v2.x narrative compat path in Module 4 is unreachable

`tasks/module_04.py:127-144` falls back to rendering a `narrative` field that
only exists in cached `runs.db` rows. But the run loop always re-prompts with
v3.0.0 SYSTEM_PROMPT — the cached path never reaches `to_blocks` in
production. Remove unless you actually replay outputs.

### N3. gpt-5.5 pricing is a placeholder

`config.py:127` carries forward gpt-4.1's per-M cost as a placeholder. Add a
banner WARNING at startup when `PROVIDER=openai` is selected, so the team
doesn't get a wildly wrong cost forecast on OpenAI swap.

### N4. JinaReaderTool uses wrong cap name

`tools/jina_reader.py:75` uses `config.TAVILY_SEARCHES_PER_TASK_CAP` for its
own cap. Rename to `READER_CALLS_PER_TASK_CAP` or `TOOL_CALLS_PER_TASK_CAP`.

### N5. Anthropic limiter at 50 RPM may bottleneck batch runs

`rate_limit.py:63` defaults to tier-1 50 rpm. A 213-account run with 5
concurrent workers will spend most of its time waiting on this limiter, not
on Anthropic. Test with `ANTHROPIC_LIMITER.rate_per_minute = 1000` (if your
tier allows) before the production batch.

### N6. `tools/web_search.py` Tavily SDK assumption

If the Tavily Python SDK ever moves to httpx, the explicit
`httpx.HTTPStatusError` branch will catch first and the string-matching
fallback becomes dead. Add a test pinning current behavior.

---

## What looks good

- **Citation system**: clean separation. `prompts/_citations.py` is the
  single source of truth; orchestrator's `_process_section_citations` and
  `_rewrite_block_citation_markers` are well-commented and handle dedup +
  per-section renumbering correctly.
- **Writeback ordering**: `writeback.write_account_outcome` orders body →
  completion props so a body failure doesn't leave `Last Researched` set.
  The `merge_status_priority` severity table is right
  (`failed` > `needs_review` > `out_of_scope` > `done`).
- **Tavily breaker**: `tools/web_search.py:40` is process-wide, lock-free
  read/write — `threading.Event.set/is_set` is atomic; no race possible.
- **JSON parsing recovery**: `tasks/base.py:340-481` is layered (fenced →
  open fence → bracket-balanced → trailing-comma repair) and handles the
  Mendix/Roblox unescaped-quote failure mode that motivated the v2.0.0
  research_pass redesign.
- **SQLite hardening**: WAL mode + 30s busy_timeout. Solid for the
  ThreadPoolExecutor write pattern.
- **Test coverage** (267/267 green): hardening, citations, ATS, canonical
  URLs, breaker, cost — all real production failure modes have corresponding
  tests. The structure shows production maturity well beyond a typical
  Phase 2 project.

---

## TODO / FIXME / HACK markers found

None. The codebase is genuinely clean of TODO/FIXME/XXX/HACK comments.

The only inline annotation found is `crm.py:131` — a non-actionable comment
about heading shape, not a TODO.
