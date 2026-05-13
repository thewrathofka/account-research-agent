"""Post-hoc cost report for runs.db.

Reads task_runs and renders cost breakdowns by account, task, and model.
Filter by time window, account, or git_sha.

Examples:
  python cost_report.py                                  # everything in runs.db
  python cost_report.py --since 7d                       # last 7 days
  python cost_report.py --since 2026-05-01               # since this ISO date
  python cost_report.py --account Oracle                 # one account
  python cost_report.py --git-sha c6b91ba                # one build
  python cost_report.py --top 20                         # top 20 in each table
  python cost_report.py --json                           # machine-readable JSON
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone

from run_log import RunLog


def _resolve_since(value: str | None) -> str | None:
    """Accept either an ISO timestamp (`2026-05-01` / `2026-05-01T12:00:00Z`)
    or a relative offset (`7d`, `24h`, `30m`). Returns an ISO string suitable
    for the `started_at >= ?` filter, or None when `value` is None."""
    if value is None:
        return None
    rel = re.fullmatch(r"(\d+)([dhm])", value.strip().lower())
    if rel:
        n, unit = int(rel.group(1)), rel.group(2)
        delta = {
            "d": timedelta(days=n),
            "h": timedelta(hours=n),
            "m": timedelta(minutes=n),
        }[unit]
        return (datetime.now(timezone.utc) - delta).isoformat()
    # Else assume already an ISO date/datetime — let SQLite string-compare handle it.
    return value


def _print_report(cs: dict, *, top: int) -> None:
    print("=" * 72)
    print(
        f"Cost: ${cs['total_usd']:.4f} "
        f"(LLM ${cs['llm_usd']:.4f}, Tavily ${cs['search_usd']:.4f})"
    )
    print(
        f"Scope: {cs['accounts']} account(s), {cs['rows']} task-run(s), "
        f"{cs['input_tokens']:,} input + {cs['output_tokens']:,} output tokens, "
        f"{cs['cached_input_tokens']:,} cached-input, {cs['searches']:,} searches"
    )
    if cs["accounts"]:
        print(f"Per-account avg: ${cs['avg_per_account_usd']:.4f}")
        per = cs["avg_per_account_usd"]
        print(
            f"Forecast at this rate: "
            f"50 accts ~${per * 50:.2f}, 213 ~${per * 213:.2f}, "
            f"1255 ~${per * 1255:.2f}"
        )
    if cs.get("failed_rows", 0):
        msg = f"⚠ Failures: {cs['failed_rows']} task-run(s) failed"
        if cs.get("failed_tavily_quota", 0):
            msg += (
                f" — {cs['failed_tavily_quota']} due to Tavily 432 "
                f"(top up at tavily.com)"
            )
        print(msg)
    print("Apify cost not included — see dashboard.apify.com.")
    print("=" * 72)

    if cs["per_account"]:
        print(f"\nPer account (top {top}):")
        for r in cs["per_account"][:top]:
            print(f"  {r['account']:<40s}  ${r['usd']:.4f}")

    if cs["per_task"]:
        print(f"\nPer task (top {top}):")
        for r in cs["per_task"][:top]:
            print(
                f"  {r['task']:<32s}  ${r['usd']:.4f}  "
                f"(in: {r['input_tokens']:>9,}  out: {r['output_tokens']:>7,}  "
                f"searches: {r['searches']:>4,})"
            )

    if cs["per_model"]:
        print(f"\nPer model (top {top}):")
        for r in cs["per_model"][:top]:
            print(
                f"  {r['model']:<24s}  ${r['usd']:.4f}  "
                f"(in: {r['input_tokens']:>9,}  out: {r['output_tokens']:>7,}  "
                f"cached-in: {r['cached_input_tokens']:>8,}, {r['rows']:>4} call(s))"
            )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="runs.db", help="SQLite run-log path (default: %(default)s)")
    p.add_argument("--since", default=None,
                   help="Filter rows with started_at >= this. ISO timestamp "
                        "(2026-05-01) OR relative offset (7d, 24h, 30m).")
    p.add_argument("--account", default=None, help="Exact account_name to filter to")
    p.add_argument("--git-sha", default=None, help="Exact git_sha to filter to")
    p.add_argument("--top", type=int, default=15, help="Rows per breakdown table (default: %(default)s)")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of tables")
    args = p.parse_args(argv)

    rl = RunLog(args.db)
    cs = rl.cost_summary(
        since=_resolve_since(args.since),
        account_name=args.account,
        git_sha=args.git_sha,
    )

    if args.json:
        print(json.dumps(cs, indent=2))
        return 0

    if cs["rows"] == 0:
        print("No task_runs rows matched the filters.")
        return 0
    _print_report(cs, top=args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
