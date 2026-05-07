# Search backend comparison — module_01_gate (2026-05-07)

Cases: stripe, oracle, razorpay
Provider: anthropic (claude-sonnet-4-5)

## Per-backend aggregates

| Backend | Schema | Field Cov | Calibration | Sources | Mean tokens | Mean searches | Latency |
|---|---|---|---|---|---|---|---|
| tavily | 100% | 88% | 100% | 100% | 6,877 | 4.0 | 18.5s |

## Per-case detail

- **tavily** / **stripe**: PASS (cov=91%, in_tok=6,192, srch=4, dur=16.9s)
- **tavily** / **oracle**: PASS (cov=91%, in_tok=8,365, srch=4, dur=19.2s)
- **tavily** / **razorpay**: PASS (cov=82%, in_tok=6,075, srch=4, dur=19.2s)

## Decision criteria (from plan §1.5a step 8)

Switch to Brave+Jina only if ALL three:
1. Calibration matches Tavily within 5pp
2. Total cost (search + tokens) drops by ≥30%
3. Latency stays within 1.5× Tavily

If criteria not met → keep Tavily (current default).
