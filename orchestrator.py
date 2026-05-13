"""Orchestrator — for each account, run each task, write results to Notion + log to SQLite."""

from __future__ import annotations

import logging
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import config
import crm as crm_module
from crm import Account, NotionCRM
from providers.base import LLMProvider
from run_log import RunLog, RunRecord, iso_now, serialize_output
from tasks import GATE_TASKS, TASK_REGISTRY
from tasks.base import Task, TaskResult
from writeback import write_account_outcome, write_gate_failure_to_notion


# Inline citation marker syntax in page-block text content. Each module emits
# module-local [N] markers; the orchestrator re-numbers them per page-body
# section (see _process_section_citations) and rewrites them into clickable
# Notion link spans (see _rewrite_block_citation_markers).
_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")


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
        label: str | None = None,
    ):
        self.crm = crm
        self.run_log = run_log
        self.provider = provider
        self.concurrency = concurrency
        self.label = label
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
        # Context envelope: maps prior task_name → its output dict. Each subsequent
        # task receives this and can read facts that earlier tasks already gathered
        # (saves search iterations on overlapping data — modules 5 + 14 use this).
        context: dict[str, Any] = {}

        for task in tasks:
            log.info("[%s] %s — running…", account.name, task.name)
            result = task.run(account.name, provider=self.provider, context=context)
            results.append(result)
            self._record_run(account, result, dry_run)

            # Add this task's output to the envelope for downstream tasks.
            if result.output is not None:
                context[task.name] = result.output

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
        Delegate the actual Notion writes to writeback.write_gate_failure_to_notion
        so sync and batch paths share the same gate-failure shape (Fix Appendix #4)."""
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
            wrote = write_gate_failure_to_notion(
                self.crm, account, results, confidence, dry_run=False,
                label=self.label,
            )
        except Exception as e:
            log.exception("[%s] Notion write failed (gate path)", account.name)
            return AccountOutcome(
                account=account, task_results=results, overall_confidence=confidence,
                overall_status="failed", wrote_to_notion=False, error=str(e),
            )

        return AccountOutcome(
            account=account, task_results=results, overall_confidence=confidence,
            overall_status="out_of_scope", wrote_to_notion=wrote,
        )

    def _record_run(self, account: Account, result: TaskResult, dry_run: bool) -> None:
        import json
        # Resolve model_tier from the task class for run-log accounting.
        task_cls = TASK_REGISTRY.get(result.task_name)
        model_tier = getattr(task_cls, "model_tier", "smart") if task_cls else "smart"
        self.run_log.record(RunRecord(
            account_page_id=account.page_id, account_name=account.name,
            task_name=result.task_name,
            started_at=iso_now(), completed_at=iso_now(),
            status="failed" if result.error else ("dry_run" if dry_run else "success"),
            confidence=result.confidence, model=result.model_used or self.provider.model,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            search_count=result.search_count, duration_seconds=result.duration_seconds,
            error=result.error,
            output_json=serialize_output(result.output),
            dry_run=dry_run,
            prompt_version=result.prompt_version,
            provider=self.provider.name,
            git_sha=self.git_sha,
            cached_input_tokens=result.cached_input_tokens,
            model_tier=model_tier,
            model_used=result.model_used,
            tool_results_seen=json.dumps(result.tool_results_seen) if result.tool_results_seen else None,
        ))

    def _write_to_notion(
        self,
        account: Account,
        results: list[TaskResult],
        overall_conf: str,
        overall_status: str,
    ) -> None:
        """Delegate to writeback.write_account_outcome — single source of truth
        for property merging, idempotent block append, and write ordering."""
        blocks = _research_section_blocks(results, label=self.label)
        write_account_outcome(
            self.crm, account, results,
            overall_status=overall_status,
            overall_confidence=overall_conf,
            research_blocks=blocks,
            dry_run=False,
            label=self.label,
        )


def _aggregate_confidence(results: list[TaskResult]) -> str:
    """Confidence excluding failed tasks. If a single non-critical task fails, it
    shouldn't drag the whole account's confidence to 'failed' — the other tasks
    succeeded. We aggregate only over non-failed results; failed tasks lower
    status to 'needs_review' (see _derive_status) without polluting confidence."""
    successful = [r for r in results if r.confidence != "failed"]
    if not successful:
        return "failed"
    worst_rank = max(_CONF_RANK[r.confidence] for r in successful)
    return next(name for name, rank in _CONF_RANK.items() if rank == worst_rank)


def _derive_status(results: list[TaskResult], overall_conf: str) -> str:
    """Status policy:
      - failed: NO usable data — gate failed OR all tasks errored
      - needs_review: some tasks errored OR overall confidence is low
      - done: every task succeeded at high/medium confidence

    Partial failure write-policy: as long as at least one non-gate task succeeded,
    we WRITE what we have to Notion with status=needs_review. Better to ship 8/9
    successful task outputs than fail-close on a single brittle module.
    """
    if not results:
        return "failed"
    if all(r.error for r in results):
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


def _process_section_citations(
    results_in_display_order: list[TaskResult],
) -> tuple[dict[str, dict[int, dict[str, Any]]], list[dict[str, Any]]]:
    """Walk every task contributing to a section IN DISPLAY ORDER, assigning
    each citation a fresh section-global number starting at 1. Returns:

      - per_task_remap: {task_name → {old_n → renumbered_citation_dict}}.
        Each task's [N] markers get rewritten using its own remap.
      - section_citations: flat list of citations in section-numbered order,
        ready to render as footnote bullets at the end of the section.

    Reading model the renumbering serves: the BDR sees [1]…[7] in one section
    pointing to seven distinct sources. Numbers are sequential and unique
    within a section, regardless of how many modules contributed.
    """
    per_task_remap: dict[str, dict[int, dict[str, Any]]] = {}
    section_citations: list[dict[str, Any]] = []
    next_n = 1
    # Track URLs already seen in THIS section so the same source cited by two
    # modules collapses to one footnote entry rather than appearing twice.
    url_to_new_citation: dict[str, dict[str, Any]] = {}
    for r in results_in_display_order:
        if r.error is not None:
            continue
        remap: dict[int, dict[str, Any]] = {}
        for c in sorted(r.citations or [], key=lambda x: x.get("n", 0)):
            old_n = c.get("n")
            url = c.get("url")
            if not isinstance(old_n, int) or not url:
                continue
            existing = url_to_new_citation.get(url)
            if existing is not None:
                remap[old_n] = existing
                continue
            new_citation = {"n": next_n, "title": c.get("title", ""), "url": url}
            remap[old_n] = new_citation
            section_citations.append(new_citation)
            url_to_new_citation[url] = new_citation
            next_n += 1
        per_task_remap[r.task_name] = remap
    return per_task_remap, section_citations


def _rewrite_block_citation_markers(
    blocks: list[dict[str, Any]],
    remap: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Walk paragraph + bulleted_list_item blocks. Any plain rich_text span
    containing `[N]` markers is split into multiple spans, with each `[N]`
    becoming a clickable Notion link pointing to the remapped citation URL.

    Spans that already carry a link annotation pass through unchanged — this
    keeps the orchestrator compatible with future modules that might prefer
    to pre-render their own links. Unmatched markers (N not in remap) are
    stripped silently; better an absent citation than a broken `[9]` to the
    reader.
    """
    out: list[dict[str, Any]] = []
    for b in blocks:
        btype = b.get("type")
        if btype not in ("paragraph", "bulleted_list_item", "numbered_list_item"):
            out.append(b)
            continue
        inner = b.get(btype) or {}
        rich_text = inner.get("rich_text") or []
        new_rich: list[dict[str, Any]] = []
        rewrote = False
        for span in rich_text:
            text = span.get("text") or {}
            content = text.get("content") or ""
            if text.get("link") or not _CITATION_MARKER_RE.search(content):
                new_rich.append(span)
                continue
            rewrote = True
            pos = 0
            for m in _CITATION_MARKER_RE.finditer(content):
                if m.start() > pos:
                    new_rich.append({
                        "type": "text",
                        "text": {"content": content[pos:m.start()]},
                    })
                old_n = int(m.group(1))
                citation = remap.get(old_n)
                if citation:
                    new_rich.append({
                        "type": "text",
                        "text": {
                            "content": f"[{citation['n']}]",
                            "link": {"url": citation["url"]},
                        },
                    })
                pos = m.end()
            if pos < len(content):
                new_rich.append({
                    "type": "text", "text": {"content": content[pos:]},
                })
        if rewrote:
            new_block = dict(b)
            new_block[btype] = {**inner, "rich_text": new_rich}
            out.append(new_block)
        else:
            out.append(b)
    return out


def _citation_footnote_bullet(c: dict[str, Any]) -> dict[str, Any]:
    """One footnote line: leading `[N]` is a clickable link to the URL; the
    title is plain text appended after for human-readable context."""
    n = c.get("n", 0)
    url = c.get("url", "")
    title = c.get("title", "")
    return {
        "object": "block", "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [
                {"type": "text", "text": {"content": f"[{n}]", "link": {"url": url}}},
                {"type": "text", "text": {"content": f" {title}" if title else ""}},
            ],
        },
    }


def _research_section_blocks(
    results: list[TaskResult],
    *,
    label: str | None = None,
) -> list[dict[str, Any]]:
    """Assemble page-body blocks in the prescribed section order.

    Each task contributes its page_blocks to (section, subsection). The orchestrator
    groups by section, then renders top-level heading_2 + optional heading_3 sub-
    sections, in the fixed order from _SECTION_ORDER.

    `label` (e.g. "phase2-baseline" or "haiku-synthesis") tags the section
    heading so multiple runs with different labels coexist on the same Notion
    page for variant comparison. None → unlabeled section that replaces any
    prior unlabeled section.

    Tasks targeting an unknown section land in a fallback "Other" section at the end.
    """
    today = date.today().isoformat()
    blocks: list[dict[str, Any]] = [
        crm_module.heading_2(crm_module.build_section_heading_text(today, label))
    ]

    # Group: (section, subsection) -> list[TaskResult]
    grouped: dict[tuple[str, str | None], list[TaskResult]] = {}
    other: list[TaskResult] = []
    for r in results:
        if r.section in _SECTION_ORDER:
            key = (r.section, r.subsection)
            grouped.setdefault(key, []).append(r)
        else:
            other.append(r)

    # v3.0.0 (2026-05-12): Global page-bottom "Sources" catch-all heading
    # removed. Every page-body module now emits inline `[N]` clickable
    # citations, making the trailing URL dump redundant noise.

    # Collect per-signal subsections from ANY task (regardless of declared
    # section). Rendered under News so every Buying Signals tag has a sourced
    # explanation right next to the rest of the news context.
    signal_subsections: list[dict[str, Any]] = []
    for r in results:
        if r.error is not None:
            continue
        signal_subsections.extend(r.signal_sections or [])
    # News section is force-emitted when there are signals to report — even if
    # no task explicitly targeted (section="News", subsection=None).
    has_news_signals = bool(signal_subsections)

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
        if not section_results and not section_errors and not (
            section == "News" and has_news_signals
        ):
            continue

        # Build a display-order list of contributors so per-section citation
        # numbering matches the visual reading order: None-subsection first,
        # then declared subsections. Per-signal subsections under News carry
        # their own per-signal sources (not citations) and don't participate
        # in this numbering.
        display_order: list[TaskResult] = []
        display_order.extend(grouped.get((section, None), []))
        for sub in _SUBSECTION_ORDER.get(section, [None]):
            if sub is None:
                continue
            display_order.extend(grouped.get((section, sub), []))
        per_task_remap, section_citations = _process_section_citations(display_order)

        blocks.append(crm_module.heading_3(section))

        # First the None-subsection content (the main body of this section).
        for r in grouped.get((section, None), []):
            remap = per_task_remap.get(r.task_name, {})
            blocks.extend(_rewrite_block_citation_markers(r.page_blocks or [], remap))

        # News-only: emit a heading_3 + logic paragraph + sources per detected
        # Buying Signal. One subsection per tag the agent wrote, so each
        # signal on the property has its provenance and reasoning on the page.
        # Per-signal subsections carry their own URL bullets directly (not
        # part of the section citation numbering).
        if section == "News" and signal_subsections:
            for sig in signal_subsections:
                signal_name = sig.get("signal") or "signal"
                blocks.append(crm_module.heading_3(signal_name))
                logic = sig.get("logic") or ""
                if logic:
                    blocks.append(crm_module.paragraph(logic))
                urls = [u for u in (sig.get("sources") or []) if u]
                if urls:
                    blocks.append(crm_module.paragraph("Sources:"))
                    for url in urls:
                        blocks.append(crm_module.bullet(url))

        # Then each declared subsection, in order.
        for sub in _SUBSECTION_ORDER.get(section, [None]):
            if sub is None:
                continue
            sub_results = grouped.get((section, sub), [])
            if not sub_results:
                continue
            blocks.append(crm_module.heading_3(f"{section} — {sub}"))
            for r in sub_results:
                remap = per_task_remap.get(r.task_name, {})
                blocks.extend(_rewrite_block_citation_markers(r.page_blocks or [], remap))

        # v3.0.0 (2026-05-12): Per-section "Sources:" footnote block removed.
        # Inline `[N]` markers are already clickable links via
        # _rewrite_block_citation_markers — the trailing bullet list was just
        # visual noise. `section_citations` is still computed so the per-task
        # remap produces correctly-numbered link spans, it's just no longer
        # rendered as a separate footnote section.

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

    blocks.append(crm_module.divider())
    return blocks


def _current_git_sha() -> str | None:
    """Return short git SHA of HEAD for run-log provenance, or None if not in a repo.
    Pinned to the repo root so a cron from a different CWD still records the SHA."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=Path(__file__).parent,
        )
        return out.decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
