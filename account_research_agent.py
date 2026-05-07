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
import sys
from datetime import date, timedelta

import config
from crm import NotionCRM, REP_KATARINA, PRIORITY_A
from orchestrator import Orchestrator
from providers import get_provider
from run_log import RunLog
from tasks import TASK_REGISTRY


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Research account profiles via the active LLM provider + tools.")
    p.add_argument("--rep", default=REP_KATARINA,
                   help="Filter by Rep (default: %(default)s)")
    p.add_argument("--priority", default=PRIORITY_A,
                   help="Filter by Priority Type (default: %(default)s)")
    p.add_argument("--limit", type=int, default=None, help="Cap on accounts")
    p.add_argument("--since", type=int, default=None, metavar="DAYS",
                   help="Only accounts not researched in the last N days")
    p.add_argument("--tasks", default="company_overview",
                   help=f"Comma-separated task names. Available: {','.join(TASK_REGISTRY)}")
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
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    crm = NotionCRM()
    run_log = RunLog(args.db)
    provider = get_provider(args.provider)

    researched_before = None
    if args.since is not None:
        researched_before = (date.today() - timedelta(days=args.since)).isoformat()

    accounts = crm.list_accounts(
        rep=args.rep, priority_type=args.priority,
        researched_before=researched_before, limit=args.limit,
    )
    if not accounts:
        print("No accounts matched. Nothing to do.")
        return 0

    print(f"Provider: {provider.name} ({provider.model})")
    print(f"Matched {len(accounts)} account(s):")
    for a in accounts:
        print(f"  - {a.name}")
    print(f"Tasks: {args.tasks}")
    print(f"Mode: {'DRY-RUN (no Notion writes)' if args.dry_run else 'LIVE (Notion writes enabled)'}")
    print(f"Concurrency: {args.concurrency}\n")

    task_names = [t.strip() for t in args.tasks.split(",") if t.strip()]

    if args.batch:
        from batch_runner import run_batch
        outcomes = run_batch(
            crm=crm, run_log=run_log, provider=provider,
            accounts=accounts, task_names=task_names, dry_run=args.dry_run,
        )
    else:
        orch = Orchestrator(crm=crm, run_log=run_log, provider=provider,
                            concurrency=args.concurrency)
        outcomes = orch.run(accounts, task_names, dry_run=args.dry_run)

    print("\n--- Summary ---")
    for o in outcomes:
        wrote = "wrote" if o.wrote_to_notion else "no-write"
        print(f"  {o.account.name}: status={o.overall_status} "
              f"conf={o.overall_confidence} ({wrote})")

    s = run_log.summary()
    print(
        f"\nLifetime run-log: {s['runs']} runs, {s['success']} ok, {s['failed']} failed, "
        f"{s['searches']} searches, "
        f"{s['input_tokens'] + s['output_tokens']:,} tokens"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
