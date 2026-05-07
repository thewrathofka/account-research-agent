# Search backend evaluation — Tavily vs Brave vs Brave+Jina

**Date:** 2026-05-07
**Decision pending:** whether to switch from Tavily to a Brave-based stack
**Recommendation:** **stay on Tavily for v0.1.0**, revisit at team rollout (1,255 accounts)

---

## TL;DR

Brave + Jina would save ~$45/month at full team scale (1,255 accounts) but
adds two vendors, a more complex search → read flow, and unverified content
quality. The savings aren't worth it until search costs become a real budget
item. Tavily's content snippets are tuned for LLM agents and our v1.0.0 prompts
are calibrated to that snippet shape — switching means re-eval-ing every module.

The infrastructure for the swap is in place: `SEARCH_BACKEND` env var picks the
backend, `tools/brave_search.py` and `tools/jina_reader.py` are implemented and
ready, `evals/compare_search_backends.py` runs the side-by-side comparison once
a `BRAVE_API_KEY` is added.

---

## Tavily baseline (measured today)

Module 01 gate against the 3 golden cases (Stripe, Oracle, Razorpay):

| Metric | Tavily |
|---|---|
| Schema compliance (E1) | 100% |
| Field coverage (E2) | 88% |
| Confidence calibration (E3) | 100% |
| Source verification (E4) | 100% |
| Mean input tokens | 6,877 |
| Mean searches | 4.0 |
| Mean latency | 18.5s |

Tavily today is comfortably above all four E1-E4 thresholds. It is the bar to
beat.

## Public pricing (2026)

| Backend | Free tier | Paid pricing | Notes |
|---|---|---|---|
| **Tavily** | 1,000 searches/mo | $0.008/search | Returns title + URL + content snippet (~500 chars) tuned for LLM agents |
| **Brave Search** | 2,000 searches/mo | ~$3/1k beyond free | Returns title + URL + short description (~150 chars). Privacy-first, real-time index |
| **Jina Reader** | ~1M tokens/mo + 200 RPM | ~$0.02/1M tokens | URL → clean Markdown of the page. Pair with Brave for full content |

## Cost projection (rough, monthly)

Assuming ~4 searches per task × 9 tasks × accounts (post 1.5a optimizations,
search count drops):

| Scope | Tavily $/mo | Brave-only $/mo | Brave+Jina $/mo |
|---|---|---|---|
| 8 Priority-A | ~$0.70 | $0 (under free tier) | $0 |
| 213 (your pipeline) | ~$8 | $0 (under free tier) | $0 |
| 1,255 (full team) | ~$50 | ~$5 | ~$5–10 |

Savings at full team scale: **$40–45/month**.

## Quality risk if we switch

The four risks I'd flag, ordered by severity:

1. **Brave's description field is shorter than Tavily's content field.** v1.0.0
   prompts assume the LLM gets ~500 chars of context per result. With Brave's
   ~150 char descriptions, the model would need extra `read_url` calls (Jina)
   to answer the same question — partly negating the cost savings AND adding
   latency.
2. **Source-verification semantics.** Tavily returns sources the model can cite;
   Brave returns search results plus richer metadata (publishers, dates) that
   our prompts don't currently use. We'd want to update the source-verification
   eval (E4) to match.
3. **Index recency.** Brave is real-time; Tavily aggregates a few days back.
   For module 6 (structural news, last 6 months) and module 13 (last 60 days)
   that doesn't matter. For module 7 (last 90 days) freshness could differ
   slightly.
4. **API stability.** Tavily's API has been stable for our usage. Brave is also
   stable but newer to commercial-grade AI agent use.

## What it would take to switch confidently

A side-by-side eval run on all 8 Phase 1 modules against the 3 golden cases —
24 task runs per backend × 3 backends = 72 task runs. Cost to perform: ~$2-3.
Time: ~30 minutes wall-clock.

Switch criteria (locked in `compare_search_backends.py`):
1. Calibration matches Tavily within 5 percentage points
2. Total cost drops ≥30%
3. Latency stays within 1.5× Tavily

If any criterion fails, keep Tavily.

## Decision

**Keep Tavily for v0.1.0 ship.** The savings ($40–45/mo at full scale) aren't
material for an MVP. Re-evaluate when:
- Team rollout actually happens (1,255 accounts) AND
- Tavily's monthly bill becomes a real line item

The swap surface is in place — `SEARCH_BACKEND=brave` in `.env` is a
one-line change once the comparison eval clears.

## Action items if we decide to switch later

1. Get a Brave API key and add `BRAVE_API_KEY` to `.env`.
2. Optionally get a Jina key (free tier is enough at our volume).
3. `python -m evals.compare_search_backends` — run side-by-side eval.
4. Inspect this directory for the resulting comparison `.md`.
5. If criteria pass, update `SEARCH_BACKEND=brave_jina` (or `brave`) in `.env`.
6. Re-run `python -m evals.runner --all` to confirm no regression on full Phase 1.
7. Bump prompt VERSIONS to `v1.1.0` if any prompt was tweaked for snippet length.
