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
from tasks import GATE_TASKS, TASK_REGISTRY
from tasks.base import Task, TaskResult
from tasks.module_01 import Module01Gate


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
        gate_failed = False

        for task in tasks:
            log.info("[%s] %s — running…", account.name, task.name)
            result = task.run(account.name, provider=self.provider)
            results.append(result)
            self._record_run(account, result, dry_run)

            # Gate enforcement: if a gate task ran and its gate_passes() is False,
            # skip ALL downstream tasks for this account.
            if task.name in GATE_TASKS and result.error is None:
                if not type(task).gate_passes(result.output):
                    gate_failed = True
                    log.info("[%s] gate %s FAILED — skipping downstream tasks",
                             account.name, task.name)
                    break

        if gate_failed:
            return self._handle_gate_failure(account, results, dry_run)

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

    def _handle_gate_failure(
        self, account: Account, results: list[TaskResult], dry_run: bool,
    ) -> AccountOutcome:
        """Gate task ran successfully but determined the account is out of scope.
        Write a minimal Notion update + reason; skip page-body assembly of full sections."""
        gate_result = next((r for r in results if r.task_name in GATE_TASKS), None)
        confidence = gate_result.confidence if gate_result else "low"
        if confidence == "failed":
            confidence = "low"

        if dry_run:
            return AccountOutcome(
                account=account, task_results=results,
                overall_confidence=confidence, overall_status="out_of_scope",
                wrote_to_notion=False,
            )

        try:
            properties: dict[str, Any] = {
                crm_module.PROP_LAST_RESEARCHED: {"date": {"start": date.today().isoformat()}},
                crm_module.PROP_RESEARCH_CONFIDENCE: {"select": {"name": confidence}},
                crm_module.PROP_RESEARCH_STATUS: {"select": {"name": "out_of_scope"}},
            }
            self.crm.update_properties(account.page_id, properties)

            # Append a small "out of scope" notice to the page body.
            blocks = [
                crm_module.heading_2(f"Research — {date.today().isoformat()}"),
                crm_module.paragraph("Out of scope: gate failed (no EU/NA operations confirmed)."),
            ]
            if gate_result and gate_result.output:
                reason = gate_result.output.get("reason_if_out_of_scope")
                if reason:
                    blocks.append(crm_module.paragraph(f"Reason: {reason}"))
            blocks.append(crm_module.divider())
            self.crm.append_blocks(account.page_id, blocks)
        except Exception as e:
            log.exception("[%s] Notion write failed (gate path)", account.name)
            return AccountOutcome(
                account=account, task_results=results, overall_confidence=confidence,
                overall_status="failed", wrote_to_notion=False, error=str(e),
            )

        return AccountOutcome(
            account=account, task_results=results, overall_confidence=confidence,
            overall_status="out_of_scope", wrote_to_notion=True,
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


# Top-level page-body section order. Tasks declare which section they write to.
_SECTION_ORDER = [
    "Overview",
    "Possible Pain Points",
    "News",
    "Creative Posture",
    "Competitor Landscape",
]

# Sub-section ordering inside each top-level section. Tasks set `subsection`.
# A None subsection means "main body of this section, before any subsections."
_SUBSECTION_ORDER: dict[str, list[str | None]] = {
    "Overview": [None, "Headcount"],
    "Possible Pain Points": [None],
    "News": [None],
    "Creative Posture": [None, "Ads Running"],
    "Competitor Landscape": [None],
}


def _research_section_blocks(results: list[TaskResult]) -> list[dict[str, Any]]:
    """Assemble page-body blocks in the prescribed section order.

    Each task contributes its page_blocks to (section, subsection). The orchestrator
    groups by section, then renders top-level heading_2 + optional heading_3 sub-
    sections, in the fixed order from _SECTION_ORDER.

    Tasks targeting an unknown section land in a fallback "Other" section at the end.
    """
    today = date.today().isoformat()
    blocks: list[dict[str, Any]] = [crm_module.heading_2(f"Research — {today}")]

    # Group: (section, subsection) -> list[TaskResult]
    grouped: dict[tuple[str, str | None], list[TaskResult]] = {}
    other: list[TaskResult] = []
    for r in results:
        if r.section in _SECTION_ORDER:
            key = (r.section, r.subsection)
            grouped.setdefault(key, []).append(r)
        else:
            other.append(r)

    # Collect sources across all tasks (deduped, ordered by first appearance).
    all_sources: list[str] = []
    seen_sources: set[str] = set()
    for r in results:
        for url in r.sources:
            if url and url not in seen_sources:
                all_sources.append(url)
                seen_sources.add(url)

    # Emit each top-level section in fixed order, with its subsections.
    for section in _SECTION_ORDER:
        section_results = [
            r for r in results
            if r.section == section and r.error is None
        ]
        section_errors = [
            r for r in results
            if r.section == section and r.error is not None
        ]
        if not section_results and not section_errors:
            continue

        blocks.append(crm_module.heading_3(section))

        # First the None-subsection content (the main body of this section).
        for r in grouped.get((section, None), []):
            blocks.extend(r.page_blocks or [])

        # Then each declared subsection, in order.
        for sub in _SUBSECTION_ORDER.get(section, [None]):
            if sub is None:
                continue
            sub_results = grouped.get((section, sub), [])
            if not sub_results:
                continue
            blocks.append(crm_module.heading_3(f"{section} — {sub}"))
            for r in sub_results:
                blocks.extend(r.page_blocks or [])

        # Emit error notes for any failed tasks in this section.
        for r in section_errors:
            blocks.append(crm_module.paragraph(f"⚠ {r.task_name} failed: {r.error}"))

    # Tasks with unknown sections land at the end so we never silently drop them.
    if other:
        blocks.append(crm_module.heading_3("Other"))
        for r in other:
            if r.error:
                blocks.append(crm_module.paragraph(f"⚠ {r.task_name} failed: {r.error}"))
                continue
            blocks.extend(r.page_blocks or [])

    # Single deduped sources block at the very end.
    if all_sources:
        blocks.append(crm_module.heading_3("Sources"))
        for url in all_sources:
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
