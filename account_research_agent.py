"""
Account Research Agent — thin entry point.

The original v0.0 single-file MVP has been split into:
  config.py           — env vars + global tuning
  crm.py              — Notion CRM adapter (read accounts, write properties + blocks)
  run_log.py          — SQLite run log (one row per task per account per run)
  orchestrator.py     — per-account loop, retries, concurrency, page-body assembly
  providers/          — model abstraction (Anthropic + OpenAI both fully implemented)
  tools/              — tool abstraction (Tavily today; jobspy lands in step 6)
  tasks/              — one Task subclass per research question
  prompts/            — versioned system prompts (one file per task, with VERSION constant)
  evals/              — golden-set eval runner + metrics (lands in step 10)
  tests/              — pytest harness for prompt loading + provider abstraction

Provider swap: set the PROVIDER env var or change the default in config.py.
  PROVIDER=openai  python account_research_agent.py --limit 1

Usage:
  python account_research_agent.py --limit 1 --dry-run     # safe smoke test
  python account_research_agent.py --limit 1               # real Notion write
  python account_research_agent.py                          # full batch
  python account_research_agent.py --since 30               # skip recently researched
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date, timedelta

import config
from crm import NotionCRM, REP_KATARINA, PRIORITY_A
from orchestrator import Orchestrator
from providers import get_provider
from run_log import RunLog, iso_now
from tasks import TASK_REGISTRY


# Accept alphanum + hyphen + underscore + dot. Trim to 40 chars. Rejection here
# is friendlier than Notion silently truncating or rejecting the heading text.
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,40}$")


def _validate_label(label: str | None) -> str | None:
    if label is None:
        return None
    label = label.strip()
    if not label:
        return None
    if not _LABEL_PATTERN.match(label):
        raise argparse.ArgumentTypeError(
            f"--label {label!r} must match {_LABEL_PATTERN.pattern} "
            "(alphanum/hyphen/underscore/dot, 1-40 chars)."
        )
    return label


def _parse_rerun(entries: list[str] | None) -> dict[str, str]:
    """Parse repeated `--rerun TASK:ACCOUNT_SUBSTRING` flags → {task: substring}.

    Surgical bypass for run-once gates that cached a wrong answer (e.g. Miro's
    M2 row from 2026-05-15 with confidence=high, total=0 from the bad slug).
    `--rerun "module_02_persona_gate:Miro"` forces the gate to re-run for any
    account whose name (lowercased) contains "miro". Other accounts replay
    their cached row normally.
    """
    if not entries:
        return {}
    out: dict[str, str] = {}
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise argparse.ArgumentTypeError(
                f"--rerun entry {entry!r} must be `task_name:ACCOUNT_SUBSTRING`."
            )
        name, substr = entry.split(":", 1)
        name = name.strip()
        substr = substr.strip()
        if name not in TASK_REGISTRY:
            raise argparse.ArgumentTypeError(
                f"--rerun: unknown task {name!r}. Known: {sorted(TASK_REGISTRY)}"
            )
        if not substr:
            raise argparse.ArgumentTypeError(
                f"--rerun: {name}: needs a non-empty account substring."
            )
        out[name] = substr
    return out


def _parse_module_since(spec: str | None) -> dict[str, int]:
    """Parse `--module-since "module_NN:DAYS,module_MM:DAYS"` → {name: days}.

    Returns {} for None/empty. Validates task names against TASK_REGISTRY and
    rejects non-positive day counts so a typo halts before the run.
    """
    if not spec:
        return {}
    out: dict[str, int] = {}
    for pair in spec.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise argparse.ArgumentTypeError(
                f"--module-since entry {pair!r} must be `task_name:DAYS`."
            )
        name, days_str = pair.split(":", 1)
        name = name.strip()
        if name not in TASK_REGISTRY:
            raise argparse.ArgumentTypeError(
                f"--module-since: unknown task {name!r}. "
                f"Known: {sorted(TASK_REGISTRY)}"
            )
        try:
            days = int(days_str.strip())
        except ValueError as e:
            raise argparse.ArgumentTypeError(
                f"--module-since: {name}:{days_str} — DAYS must be an integer."
            ) from e
        if days <= 0:
            raise argparse.ArgumentTypeError(
                f"--module-since: {name}:{days} — DAYS must be positive."
            )
        out[name] = days
    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Research account profiles via the active LLM provider + tools.")
    p.add_argument("--rep", default=REP_KATARINA,
                   help="Filter by Rep (default: %(default)s)")
    p.add_argument("--priority", default=PRIORITY_A,
                   help="Filter by Priority Type (default: %(default)s)")
    p.add_argument("--limit", type=int, default=None, help="Cap on accounts")
    p.add_argument("--account", default=None,
                   help="Run only the named account (substring match on title). "
                        "Useful for single-account smoke tests targeting a "
                        "specific company.")
    p.add_argument("--since", type=int, default=None, metavar="DAYS",
                   help="Only accounts not researched in the last N days "
                        "(account-level filter via the Notion `Last Researched` "
                        "property; the monthly full-pipeline run is the only "
                        "job that writes that property)")
    p.add_argument("--module-since", default=None, metavar="SPEC",
                   help="Per-module freshness gating. Comma-separated "
                        "`module_NN:DAYS` pairs (e.g. "
                        "'module_06_structural_news:1,module_07_trigger_events:1,"
                        "module_14_hiring_signal:7'). Modules with a fresh "
                        "successful row within the threshold are skipped; the "
                        "prior output is injected into the context envelope "
                        "for downstream tasks. Modules not listed run normally.")
    p.add_argument("--rerun", action="append", default=None, metavar="TASK:ACCOUNT",
                   help="Surgically force a (task, account) pair to bypass "
                        "the run-once skip rule. Format: "
                        "`module_02_persona_gate:Miro`. Repeatable. "
                        "ACCOUNT is a case-insensitive substring match against "
                        "the Notion account title. Only the matching task on "
                        "matching accounts re-runs; everything else replays "
                        "cached output as usual. Use to invalidate a single "
                        "wrong cached gate decision without disrupting the "
                        "rest of the batch.")
    from tasks import PHASE2_TASKS as _default_tasks
    p.add_argument("--tasks", default=",".join(_default_tasks),
                   help=(
                       "Comma-separated task names. Defaults to the full "
                       "Phase 2 pipeline (12 tasks, ~$0.30/account). "
                       f"Available: {','.join(TASK_REGISTRY)}"
                   ))
    p.add_argument("--dry-run", action="store_true",
                   help="Run research but skip Notion writes (still logs to SQLite)")
    p.add_argument("--batch", action="store_true",
                   help="Use the Anthropic Message Batches API for synthesis tasks "
                        "(~50%% cheaper, ~hours wait). Sync gate+research, batched "
                        "synthesis, sync tool-using tasks. Anthropic only for now.")
    p.add_argument("--concurrency", type=int, default=config.DEFAULT_CONCURRENCY)
    p.add_argument("--db", default="runs.db", help="SQLite run-log path")
    p.add_argument("--provider", default=None,
                   help="Override active provider (anthropic|openai|gemini). "
                        "Default: PROVIDER env var or 'anthropic'.")
    p.add_argument("--cost-summary", action="store_true",
                   help="At end of run, print a verbose cost breakdown "
                        "(per-account, per-task, per-model). Always shows the "
                        "batch total $; this flag adds the detail tables.")
    p.add_argument("--label", default=None,
                   help="Tag the research section heading with this label "
                        "(e.g. 'phase2-baseline' or 'haiku-synthesis'). "
                        "Reruns with the SAME label overwrite only that "
                        "label's prior section, so multiple variants coexist "
                        "on each Notion page for side-by-side comparison. "
                        "Without --label, behaviour is unchanged (the single "
                        "unlabeled section gets overwritten each run).")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)
    args.label = _validate_label(args.label)
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.verbose:
        for noisy in ("httpx", "httpcore", "urllib3", "JobSpy"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    crm = NotionCRM()
    # Fail loudly if Notion's schema drifted from EXPECTED_NOTION_PROPERTIES
    # (Fix Appendix #20). Better to halt now than write garbage to Notion.
    try:
        crm.validate_schema()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    run_log = RunLog(args.db)
    provider = get_provider(args.provider)

    researched_before = None
    if args.since is not None:
        researched_before = (date.today() - timedelta(days=args.since)).isoformat()

    accounts = crm.list_accounts(
        rep=args.rep, priority_type=args.priority,
        researched_before=researched_before, limit=None,  # apply --limit AFTER --account filter
    )
    if args.account:
        needle = args.account.strip().lower()
        accounts = [a for a in accounts if needle in a.name.lower()]
    if args.limit is not None:
        accounts = accounts[: args.limit]
    if not accounts:
        if args.account:
            print(f"No account matched --account={args.account!r} under "
                  f"rep={args.rep!r} priority={args.priority!r}. Nothing to do.")
        else:
            print("No accounts matched. Nothing to do.")
        return 0

    print(f"Provider: {provider.name} ({provider.model})")
    print(f"Matched {len(accounts)} account(s):")
    for a in accounts:
        print(f"  - {a.name}")
    print(f"Tasks: {args.tasks}")
    print(f"Mode: {'DRY-RUN (no Notion writes)' if args.dry_run else 'LIVE (Notion writes enabled)'}")
    if args.label:
        print(f"Label: {args.label} (research section heading: "
              f"'Research — {date.today().isoformat()} — {args.label}')")
    print(f"Concurrency: {args.concurrency}\n")

    task_names = [t.strip() for t in args.tasks.split(",") if t.strip()]

    # Phase A: parse --module-since "module_NN:DAYS,module_MM:DAYS" → dict.
    module_since = _parse_module_since(args.module_since)
    rerun = _parse_rerun(args.rerun)
    if rerun:
        print(f"Rerun overrides: {rerun}")

    # Stamp the run boundary BEFORE work begins so the cost summary can filter
    # task_runs rows to just-this-batch (vs lifetime totals).
    batch_started_at = iso_now()

    if args.batch:
        from batch_runner import run_batch
        outcomes = run_batch(
            crm=crm, run_log=run_log, provider=provider,
            accounts=accounts, task_names=task_names, dry_run=args.dry_run,
            label=args.label,
        )
    else:
        orch = Orchestrator(crm=crm, run_log=run_log, provider=provider,
                            concurrency=args.concurrency, label=args.label,
                            module_since=module_since, rerun=rerun)
        outcomes = orch.run(accounts, task_names, dry_run=args.dry_run)

    print("\n--- Summary ---")
    for o in outcomes:
        wrote = "wrote" if o.wrote_to_notion else "no-write"
        print(f"  {o.account.name}: status={o.overall_status} "
              f"conf={o.overall_confidence} ({wrote})")

    _print_cost_block(run_log, batch_started_at, verbose=args.cost_summary,
                      account_count=len(accounts))

    s = run_log.summary()
    print(
        f"\nLifetime run-log: {s['runs']} runs, {s['success']} ok, {s['failed']} failed, "
        f"{s['searches']} searches, "
        f"{s['input_tokens'] + s['output_tokens']:,} tokens"
    )
    return 0


def _print_cost_block(
    run_log: RunLog,
    batch_started_at: str,
    *,
    verbose: bool,
    account_count: int,
) -> None:
    """Render the batch cost block at end of run.

    Always prints the totals + forecast (~3 lines). With --cost-summary it
    also prints per-account, per-task, and per-model breakdown tables.
    Apify spend is NOT included (see config.py — accumulated on Apify
    dashboard, not in runs.db).
    """
    cs = run_log.cost_summary(since=batch_started_at)
    print("\n--- Cost (this batch) ---")
    print(
        f"  Total: ${cs['total_usd']:.4f} "
        f"(LLM ${cs['llm_usd']:.4f} + Tavily ${cs['search_usd']:.4f}) "
        f"across {cs['accounts']} account(s), {cs['rows']} task-run(s)"
    )
    if cs["accounts"]:
        print(
            f"  Per account avg: ${cs['avg_per_account_usd']:.4f}  "
            f"(input: {cs['input_tokens']:,}  output: {cs['output_tokens']:,}  "
            f"cached-input: {cs['cached_input_tokens']:,}  "
            f"searches: {cs['searches']})"
        )
    # Surface failed task-runs prominently — silent failures with a happy
    # cost total were the original sin that motivated this block.
    if cs.get("failed_rows", 0):
        print(f"  ⚠ Failures: {cs['failed_rows']} task-run(s) failed")
    # Tavily quota exhaustion can degrade success rows too (model sees the
    # 432 in a tool_result and proceeds with low confidence). Surface both
    # cases as a single "affected" count so failed + degraded show up.
    if cs.get("tavily_quota_affected", 0):
        print(
            f"  ⚠ Tavily quota: {cs['tavily_quota_affected']} row(s) saw the "
            f"432 short-circuit (failed or degraded) — top up at tavily.com"
        )
    # Always print Apify-not-included caveat so the user isn't surprised.
    print("  Apify (LinkedIn/Meta/TikTok ad scrapers) not included — check "
          "dashboard.apify.com.")

    # Forecast: extrapolate per-account cost to common scale points.
    if cs["avg_per_account_usd"] > 0:
        per = cs["avg_per_account_usd"]
        print(
            f"  Forecast at this per-account rate: "
            f"50 accts ~${per * 50:.2f}, 213 ~${per * 213:.2f}, "
            f"1255 ~${per * 1255:.2f}"
        )

    if not verbose:
        return

    if cs["per_account"]:
        print("\n  Per-account $:")
        for r in cs["per_account"]:
            print(f"    {r['account']:<40s}  ${r['usd']:.4f}")
    if cs["per_task"]:
        print("\n  Per-task $:")
        for r in cs["per_task"]:
            print(
                f"    {r['task']:<32s}  ${r['usd']:.4f}  "
                f"(in: {r['input_tokens']:>7,}  out: {r['output_tokens']:>5,}  "
                f"searches: {r['searches']:>3,})"
            )
    if cs["per_model"]:
        print("\n  Per-model $:")
        for r in cs["per_model"]:
            print(
                f"    {r['model']:<24s}  ${r['usd']:.4f}  "
                f"(in: {r['input_tokens']:>7,}  out: {r['output_tokens']:>5,}  "
                f"cached-in: {r['cached_input_tokens']:>6,}, {r['rows']:>3} call(s))"
            )


if __name__ == "__main__":
    sys.exit(main())
