"""Batch runner — process synthesis tasks for many accounts in one Anthropic Message
Batch (~50% cheaper than sync).

This is a focused implementation for the monthly batch use case:

  Pass 1 (sync, parallel per account):
    module_01_gate + research_pass for every account

  Pass 2 (BATCHED across all accounts):
    All synthesis-only modules — one batch with N_accounts × N_synthesis_tasks
    requests submitted in a single API call

  Pass 3 (sync, parallel per account):
    Tool-using modules (9, 14) — they need agent loops, can't batch

Pass 1 stays sync because gate must short-circuit and research_pass is per-account.
Pass 3 stays sync because jobspy / tool calls need iterative reasoning.

Pass 2 is the big win: the synthesis tasks are independent, one-shot, and there
can be many of them per monthly batch (213 accounts × 6 synthesis modules = 1,278
batched requests, all at 50% off).

Usage: account_research_agent.py --batch
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from crm import Account, NotionCRM
from orchestrator import (
    AccountOutcome, _aggregate_confidence, _derive_status,
    _research_section_blocks,
)
from providers.base import BatchRequest, LLMProvider
from run_log import RunLog, RunRecord, iso_now, serialize_output
from tasks import GATE_TASKS, TASK_REGISTRY
from tasks.base import Task, TaskResult


log = logging.getLogger(__name__)


def run_batch(
    crm: NotionCRM,
    run_log: RunLog,
    provider: LLMProvider,
    accounts: list[Account],
    task_names: list[str],
    dry_run: bool = False,
    poll_interval_s: int = 30,
    max_wait_s: int = 24 * 3600,
) -> list[AccountOutcome]:
    """Three-pass batch run. Returns AccountOutcome per account."""

    if not provider.supports_batch:
        raise RuntimeError(
            f"Provider {provider.name!r} does not support batch mode. "
            f"Use sync orchestrator instead, or switch providers via --provider anthropic."
        )

    # Categorize tasks: pre-batch (sync, agent loops), batchable (synthesis-only),
    # post-batch (sync, tool-using).
    pre_batch_names = []
    batchable_names = []
    post_batch_names = []
    for name in task_names:
        cls = TASK_REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"Unknown task: {name}")
        if name in GATE_TASKS or name == "research_pass":
            pre_batch_names.append(name)
        elif getattr(cls, "synthesis_only", False):
            batchable_names.append(name)
        else:
            post_batch_names.append(name)

    log.info("Batch mode: pre=%s | batched=%s | post=%s",
             pre_batch_names, batchable_names, post_batch_names)

    # ---- Pass 1: sync gate + research_pass per account ----
    per_account_results: dict[str, list[TaskResult]] = {a.page_id: [] for a in accounts}
    per_account_context: dict[str, dict[str, Any]] = {a.page_id: {} for a in accounts}
    per_account_gated: dict[str, bool] = {a.page_id: False for a in accounts}

    log.info("Pass 1 — running %d pre-batch tasks for %d accounts (sync)",
             len(pre_batch_names), len(accounts))
    pre_tasks = [TASK_REGISTRY[n]() for n in pre_batch_names]
    with ThreadPoolExecutor(max_workers=config.DEFAULT_CONCURRENCY) as pool:
        futures = {
            pool.submit(_run_pre_batch, acc, pre_tasks, provider, run_log, dry_run): acc
            for acc in accounts
        }
        for fut in as_completed(futures):
            acc = futures[fut]
            results, context, gate_failed = fut.result()
            per_account_results[acc.page_id] = results
            per_account_context[acc.page_id] = context
            per_account_gated[acc.page_id] = gate_failed

    # ---- Pass 2: BATCH synthesis tasks across all surviving accounts ----
    surviving = [a for a in accounts if not per_account_gated[a.page_id]]
    log.info("Pass 2 — batching %d synthesis tasks across %d accounts (gate-passing)",
             len(batchable_names), len(surviving))

    if surviving and batchable_names:
        batch_results = _run_batch_pass(
            surviving, batchable_names, per_account_context, provider,
            poll_interval_s=poll_interval_s, max_wait_s=max_wait_s,
        )
        for (page_id, task_name), task_result in batch_results.items():
            per_account_results[page_id].append(task_result)
            run_log.record(_to_run_record(
                next(a for a in accounts if a.page_id == page_id),
                task_result, dry_run, provider.name, _git_sha_safe(),
            ))

    # ---- Pass 3: sync tool-using tasks (modules 9, 14) ----
    if surviving and post_batch_names:
        log.info("Pass 3 — running %d tool-using tasks for %d accounts (sync)",
                 len(post_batch_names), len(surviving))
        post_tasks = [TASK_REGISTRY[n]() for n in post_batch_names]
        with ThreadPoolExecutor(max_workers=config.DEFAULT_CONCURRENCY) as pool:
            futures = {
                pool.submit(_run_post_batch, acc, post_tasks, provider, run_log, dry_run,
                            per_account_context[acc.page_id]): acc
                for acc in surviving
            }
            for fut in as_completed(futures):
                acc = futures[fut]
                results = fut.result()
                per_account_results[acc.page_id].extend(results)

    # Build outcomes + write to Notion if not dry-run
    outcomes: list[AccountOutcome] = []
    for acc in accounts:
        results = per_account_results[acc.page_id]
        if per_account_gated[acc.page_id]:
            outcomes.append(AccountOutcome(
                account=acc, task_results=results, overall_confidence="high",
                overall_status="out_of_scope", wrote_to_notion=False,
            ))
            continue
        conf = _aggregate_confidence(results)
        status = _derive_status(results, conf)
        if dry_run or status == "failed":
            outcomes.append(AccountOutcome(
                account=acc, task_results=results, overall_confidence=conf,
                overall_status=status, wrote_to_notion=False,
            ))
            continue
        try:
            _write_outcome_to_notion(crm, acc, results, conf, status)
            outcomes.append(AccountOutcome(
                account=acc, task_results=results, overall_confidence=conf,
                overall_status=status, wrote_to_notion=True,
            ))
        except Exception as e:
            log.exception("[%s] Notion write failed", acc.name)
            outcomes.append(AccountOutcome(
                account=acc, task_results=results, overall_confidence=conf,
                overall_status="failed", wrote_to_notion=False, error=str(e),
            ))

    return outcomes


# ---- Pass 1 helper ----

def _run_pre_batch(
    account: Account, tasks: list[Task], provider: LLMProvider,
    run_log: RunLog, dry_run: bool,
) -> tuple[list[TaskResult], dict[str, Any], bool]:
    """Run gate + research_pass synchronously for one account. Records to SQLite."""
    results: list[TaskResult] = []
    context: dict[str, Any] = {}
    gate_failed = False
    for task in tasks:
        log.info("[%s] %s (pre-batch)", account.name, task.name)
        result = task.run(account.name, provider=provider, context=context)
        results.append(result)
        run_log.record(_to_run_record(account, result, dry_run, provider.name, _git_sha_safe()))
        if result.output is not None:
            context[task.name] = result.output
        if task.name in GATE_TASKS and result.error is None:
            if not type(task).gate_passes(result.output):
                gate_failed = True
                log.info("[%s] gate FAILED — skipping all downstream tasks", account.name)
                break
    return results, context, gate_failed


# ---- Pass 2 helper (the actual batch submission) ----

def _run_batch_pass(
    accounts: list[Account], task_names: list[str],
    per_account_context: dict[str, dict[str, Any]],
    provider: LLMProvider,
    poll_interval_s: int, max_wait_s: int,
) -> dict[tuple[str, str], TaskResult]:
    """Build BatchRequests for every (account, synthesis-task) pair, submit one batch,
    poll, and distribute results back keyed by (page_id, task_name)."""
    import hashlib
    batch_requests: list[BatchRequest] = []
    request_index: dict[str, tuple[Account, Task, str]] = {}  # custom_id → (account, task, prompt_version)
    for acc in accounts:
        ctx = per_account_context[acc.page_id]
        for task_name in task_names:
            task = TASK_REGISTRY[task_name]()
            system_prompt = task.prompt_module.SYSTEM_PROMPT
            prompt_version = getattr(task.prompt_module, "VERSION", "unknown")
            user_message = task.build_user_message(acc.name, ctx)
            # Anthropic caps custom_id at 64 chars; UUID + task_name overflows.
            # Hash the page_id to 12 hex chars; combined with task_name (≤30 chars)
            # we stay well under the cap and the mapping is unique per (acc, task).
            page_hash = hashlib.sha1(acc.page_id.encode()).hexdigest()[:12]
            custom_id = f"{page_hash}__{task_name}"
            batch_requests.append(BatchRequest(
                custom_id=custom_id,
                system_prompt=system_prompt,
                user_message=user_message,
                model_tier=task.model_tier,
            ))
            request_index[custom_id] = (acc, task, prompt_version)

    log.info("Submitting batch of %d requests", len(batch_requests))
    handle = provider.submit_batch(batch_requests)
    log.info("Batch submitted: %s (status=%s)", handle.batch_id, handle.expected_status)

    # Poll until ended
    start = time.time()
    while True:
        status = provider.poll_batch(handle)
        if status == "ended":
            break
        if status == "errored":
            raise RuntimeError(f"Batch {handle.batch_id} errored")
        if time.time() - start > max_wait_s:
            raise TimeoutError(f"Batch {handle.batch_id} not done after {max_wait_s}s")
        log.info("  batch status=%s, waiting %ds…", status, poll_interval_s)
        time.sleep(poll_interval_s)

    log.info("Batch %s ended; fetching results", handle.batch_id)
    raw_results = provider.fetch_batch_results(handle)

    # Translate to TaskResults
    out: dict[tuple[str, str], TaskResult] = {}
    for custom_id, provider_result in raw_results.items():
        if custom_id not in request_index:
            log.warning("Stray custom_id in batch results: %s", custom_id)
            continue
        acc, task, prompt_version = request_index[custom_id]
        from tasks.base import _extract_json
        if provider_result.error or not provider_result.text:
            out[(acc.page_id, task.name)] = TaskResult(
                task_name=task.name, output=None, confidence="failed",
                fields={}, page_blocks=[], section=task.section, subsection=task.subsection,
                sources=[], search_count=0,
                input_tokens=provider_result.input_tokens,
                output_tokens=provider_result.output_tokens,
                duration_seconds=0.0,
                prompt_version=prompt_version, provider_name=provider.name,
                cached_input_tokens=provider_result.cached_input_tokens,
                model_used=provider_result.model_used,
                error=provider_result.error or "empty batch response",
            )
            continue
        output = _extract_json(provider_result.text)
        if output is None:
            out[(acc.page_id, task.name)] = TaskResult(
                task_name=task.name, output=None, confidence="failed",
                fields={}, page_blocks=[], section=task.section, subsection=task.subsection,
                sources=[], search_count=0,
                input_tokens=provider_result.input_tokens,
                output_tokens=provider_result.output_tokens,
                duration_seconds=0.0,
                prompt_version=prompt_version, provider_name=provider.name,
                cached_input_tokens=provider_result.cached_input_tokens,
                model_used=provider_result.model_used,
                error=f"batch end_turn without JSON: {provider_result.text[:300]}",
            )
            continue
        confidence = output.get("confidence", "low")
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        out[(acc.page_id, task.name)] = TaskResult(
            task_name=task.name, output=output, confidence=confidence,
            fields=task.to_fields(output), page_blocks=task.to_blocks(output),
            section=task.section, subsection=task.subsection,
            sources=output.get("sources", []) or [],
            search_count=0,
            input_tokens=provider_result.input_tokens,
            output_tokens=provider_result.output_tokens,
            duration_seconds=0.0,
            prompt_version=prompt_version, provider_name=provider.name,
            cached_input_tokens=provider_result.cached_input_tokens,
            model_used=provider_result.model_used,
        )
    return out


# ---- Pass 3 helper ----

def _run_post_batch(
    account: Account, tasks: list[Task], provider: LLMProvider,
    run_log: RunLog, dry_run: bool, context: dict[str, Any],
) -> list[TaskResult]:
    """Run tool-using tasks (modules 9, 14) synchronously for one account."""
    results: list[TaskResult] = []
    for task in tasks:
        log.info("[%s] %s (post-batch)", account.name, task.name)
        result = task.run(account.name, provider=provider, context=context)
        results.append(result)
        run_log.record(_to_run_record(account, result, dry_run, provider.name, _git_sha_safe()))
    return results


# ---- shared helpers ----

def _to_run_record(
    account: Account, result: TaskResult, dry_run: bool,
    provider_name: str, git_sha: str | None,
) -> RunRecord:
    task_cls = TASK_REGISTRY.get(result.task_name)
    model_tier = getattr(task_cls, "model_tier", "smart") if task_cls else "smart"
    return RunRecord(
        account_page_id=account.page_id, account_name=account.name,
        task_name=result.task_name,
        started_at=iso_now(), completed_at=iso_now(),
        status="failed" if result.error else ("dry_run" if dry_run else "success"),
        confidence=result.confidence, model=result.model_used,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        search_count=result.search_count, duration_seconds=result.duration_seconds,
        error=result.error,
        output_json=serialize_output(result.output),
        dry_run=dry_run,
        prompt_version=result.prompt_version,
        provider=provider_name,
        git_sha=git_sha,
        cached_input_tokens=result.cached_input_tokens,
        model_tier=model_tier,
        model_used=result.model_used,
    )


def _git_sha_safe() -> str | None:
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return None


def _write_outcome_to_notion(
    crm: NotionCRM, account: Account, results: list[TaskResult],
    overall_conf: str, overall_status: str,
) -> None:
    """Same as Orchestrator._write_to_notion — extracted to share with batch path."""
    import crm as crm_module
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
    properties[crm_module.PROP_LAST_RESEARCHED] = {"date": {"start": date.today().isoformat()}}
    properties[crm_module.PROP_RESEARCH_CONFIDENCE] = {
        "select": {"name": overall_conf if overall_conf != "failed" else "low"}
    }
    properties[crm_module.PROP_RESEARCH_STATUS] = {"select": {"name": overall_status}}

    crm.update_properties(account.page_id, properties)
    blocks = _research_section_blocks(results)
    if blocks:
        crm.append_blocks(account.page_id, blocks)
