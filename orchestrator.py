"""Orchestrator — for each account, run each task, write results to Notion + log to SQLite."""

from __future__ import annotations

import json
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from typing import Any

import config
import crm as crm_module
from crm import Account, NotionCRM
from providers.base import LLMProvider
from run_log import RunLog, RunRecord, iso_now, serialize_output
from tasks import TASK_REGISTRY
from tasks.base import Task, TaskResult


log = logging.getLogger(__name__)


# Confidence ranking: lower = better. Used to aggregate per-task confidences.
_CONF_RANK = {"high": 0, "medium": 1, "low": 2, "failed": 3}


@dataclass
class AccountOutcome:
    account: Account
    task_results: list[TaskResult]
    overall_confidence: str
    overall_status: str          # done | needs_review | failed | out_of_scope
    wrote_to_notion: bool
    error: str | None = None


class Orchestrator:
    def __init__(
        self,
        crm: NotionCRM,
        run_log: RunLog,
        provider: LLMProvider,
        concurrency: int = config.DEFAULT_CONCURRENCY,
    ):
        self.crm = crm
        self.run_log = run_log
        self.provider = provider
        self.concurrency = concurrency
        self.git_sha = _current_git_sha()

    def run(
        self,
        accounts: list[Account],
        task_names: list[str],
        dry_run: bool = False,
    ) -> list[AccountOutcome]:
        unknown = [n for n in task_names if n not in TASK_REGISTRY]
        if unknown:
            raise ValueError(f"Unknown task(s): {unknown}. Known: {list(TASK_REGISTRY)}")
        tasks = [TASK_REGISTRY[n]() for n in task_names]

        outcomes: list[AccountOutcome] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {
                pool.submit(self._process_account, acc, tasks, dry_run): acc
                for acc in accounts
            }
            for fut in as_completed(futures):
                acc = futures[fut]
                try:
                    outcome = fut.result()
                except Exception as e:
                    log.exception("[%s] account failed", acc.name)
                    outcome = AccountOutcome(
                        account=acc, task_results=[], overall_confidence="failed",
                        overall_status="failed", wrote_to_notion=False, error=str(e),
                    )
                outcomes.append(outcome)
                ok = sum(1 for r in outcome.task_results if r.error is None)
                log.info(
                    "[%s] %s | conf=%s | %d/%d ok | %s",
                    outcome.account.name, outcome.overall_status,
                    outcome.overall_confidence, ok, len(outcome.task_results),
                    "WROTE" if outcome.wrote_to_notion else "no-write",
                )
        return outcomes

    # ---- internal ----

    def _process_account(
        self, account: Account, tasks: list[Task], dry_run: bool,
    ) -> AccountOutcome:
        results: list[TaskResult] = []
        for task in tasks:
            log.info("[%s] %s — running…", account.name, task.name)
            result = task.run(account.name, provider=self.provider)
            results.append(result)
            self._record_run(account, result, dry_run)

        overall_conf = _aggregate_confidence(results)
        overall_status = _derive_status(results, overall_conf)

        if dry_run or overall_status == "failed":
            return AccountOutcome(
                account=account, task_results=results, overall_confidence=overall_conf,
                overall_status=overall_status, wrote_to_notion=False,
            )

        try:
            self._write_to_notion(account, results, overall_conf, overall_status)
        except Exception as e:
            log.exception("[%s] Notion write failed", account.name)
            return AccountOutcome(
                account=account, task_results=results, overall_confidence=overall_conf,
                overall_status="failed", wrote_to_notion=False, error=str(e),
            )

        return AccountOutcome(
            account=account, task_results=results, overall_confidence=overall_conf,
            overall_status=overall_status, wrote_to_notion=True,
        )

    def _record_run(self, account: Account, result: TaskResult, dry_run: bool) -> None:
        self.run_log.record(RunRecord(
            account_page_id=account.page_id, account_name=account.name,
            task_name=result.task_name,
            started_at=iso_now(), completed_at=iso_now(),
            status="failed" if result.error else ("dry_run" if dry_run else "success"),
            confidence=result.confidence, model=self.provider.model,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            search_count=result.search_count, duration_seconds=result.duration_seconds,
            error=result.error,
            output_json=serialize_output(result.output),
            dry_run=dry_run,
            prompt_version=result.prompt_version,
            provider=self.provider.name,
            git_sha=self.git_sha,
        ))

    def _write_to_notion(
        self,
        account: Account,
        results: list[TaskResult],
        overall_conf: str,
        overall_status: str,
    ) -> None:
        # Merge per-task field updates. Multi-selects are unioned.
        properties: dict[str, Any] = {}
        for r in results:
            for k, v in r.fields.items():
                if (
                    k in (crm_module.PROP_BUYING_SIGNALS, crm_module.PROP_BUYING_INTENT)
                    and k in properties
                    and "multi_select" in properties[k]
                ):
                    existing = properties[k]["multi_select"]
                    seen = {item["name"] for item in existing}
                    for item in v["multi_select"]:
                        if item["name"] not in seen:
                            existing.append(item)
                            seen.add(item["name"])
                else:
                    properties[k] = v

        # Always write agent-managed metadata.
        properties[crm_module.PROP_LAST_RESEARCHED] = {"date": {"start": date.today().isoformat()}}
        properties[crm_module.PROP_RESEARCH_CONFIDENCE] = {
            "select": {"name": overall_conf if overall_conf != "failed" else "low"}
        }
        properties[crm_module.PROP_RESEARCH_STATUS] = {"select": {"name": overall_status}}

        self.crm.update_properties(account.page_id, properties)

        blocks = _research_section_blocks(results)
        if blocks:
            self.crm.append_blocks(account.page_id, blocks)


def _aggregate_confidence(results: list[TaskResult]) -> str:
    if not results:
        return "failed"
    worst_rank = max(_CONF_RANK[r.confidence] for r in results)
    return next(name for name, rank in _CONF_RANK.items() if rank == worst_rank)


def _derive_status(results: list[TaskResult], overall_conf: str) -> str:
    if not results or all(r.error for r in results):
        return "failed"
    if overall_conf == "failed":
        return "failed"
    if overall_conf == "low" or any(r.error for r in results):
        return "needs_review"
    return "done"


def _research_section_blocks(results: list[TaskResult]) -> list[dict[str, Any]]:
    """Assemble page-body blocks. Section-aware refactor lands in step 9 of Phase 1.

    For now this preserves the v0.0 shape: one heading per task. Step 9 will replace
    this with section-grouped assembly in the prescribed order.
    """
    today = date.today().isoformat()
    blocks: list[dict[str, Any]] = [crm_module.heading_2(f"Research — {today}")]
    for r in results:
        blocks.append(crm_module.heading_3(r.task_name))
        if r.error:
            blocks.append(crm_module.paragraph(f"Failed: {r.error}"))
            continue
        blocks.extend(r.page_blocks)
        if r.sources:
            blocks.append(crm_module.paragraph("Sources:"))
            for url in r.sources:
                blocks.append(crm_module.bullet(url))
    blocks.append(crm_module.divider())
    return blocks


def _current_git_sha() -> str | None:
    """Return short git SHA of HEAD for run-log provenance, or None if not in a repo."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        )
        return out.decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
