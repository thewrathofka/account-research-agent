"""
Account Research Agent — MVP
============================
A monthly batch agent that researches accounts in a Notion CRM ("All Accounts"
inside Money Moguls CRM) and writes structured findings + a free-form research
note back to each account's Notion page.

Pipeline:
  CLI  → list accounts from Notion (filtered by Rep + Priority)
       → for each account, run each research task (Claude + Tavily ReAct loop)
       → log every run to SQLite
       → write task outputs back to Notion (skipped on --dry-run)

To add a new research question, write a new Task subclass and register it in
TASK_REGISTRY at the bottom of the file.

Usage:
  python account_research_agent.py --limit 1 --dry-run     # safe smoke test
  python account_research_agent.py --limit 1               # real Notion write
  python account_research_agent.py                          # full batch
  python account_research_agent.py --since 30               # skip recently researched
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from anthropic import Anthropic
from dotenv import load_dotenv
from notion_client import Client as NotionClient
from tavily import TavilyClient


# ============================================================================
# 1. CONFIG — env loading + global constants
# ============================================================================

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

for _name, _value in {
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "TAVILY_API_KEY": TAVILY_API_KEY,
    "NOTION_API_KEY": NOTION_API_KEY,
    "NOTION_DATABASE_ID": NOTION_DATABASE_ID,
}.items():
    if not _value or "PLACEHOLDER" in _value:
        print(f"ERROR: {_name} is missing or still a placeholder in .env")
        sys.exit(1)

MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 4096
MAX_AGENT_ITERATIONS = 10
DEFAULT_CONCURRENCY = 5
TAVILY_SEARCHES_PER_TASK_CAP = 8


# ============================================================================
# 2. NOTION SCHEMA — property names + option vocabularies
# Centralized so a Notion rename is a one-line change.
# ============================================================================

# Property names — exact strings (note trailing space on Company Structure)
PROP_ACCOUNT_NAME = "Account Name"
PROP_REP = "Rep"
PROP_PRIORITY_TYPE = "Priority Type"
PROP_SIZE = "Size"
PROP_BUYING_SIGNALS = "Buying Signals"
PROP_LEAD_SIGNAL = "Lead Signal"
PROP_NEW_HIRE = "New Hire"
PROP_COMPANY_STRUCTURE = "Company Structure "  # trailing space is intentional
PROP_PARENT_CHILD = "Parent-Child"
PROP_NAME_OF_PARENT = "Name of Parent"

# Agent-managed properties (added 2026-05-06)
PROP_LAST_RESEARCHED = "Last Researched"
PROP_RESEARCH_CONFIDENCE = "Research Confidence"
PROP_RESEARCH_STATUS = "Research Status"

# Option vocabularies — must match Notion option names exactly
SIZE_OPTIONS = {"<1000", "1000-2000", "2000-5000", "5000+"}
BUYING_SIGNAL_OPTIONS = {
    "unify mod", "unify high", "sales nav mod", "sales nav high",
    "industry", "cluster", "CW competitor", "MQA", "hiring", "downsizing",
}
LEAD_SIGNAL_OPTIONS = {"TOFU", "MQL", "call request", "new hire"}

REP_KATARINA = "Katarina"
PRIORITY_A = "Priority A"


# ============================================================================
# 3. RUN LOG — SQLite, one row per (account, task, run)
# ============================================================================

_RUN_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_page_id TEXT NOT NULL,
    account_name TEXT NOT NULL,
    task_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,                  -- success | failed | dry_run
    confidence TEXT,                       -- high | medium | low | failed
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    search_count INTEGER DEFAULT 0,
    duration_seconds REAL,
    error TEXT,
    output_json TEXT,
    dry_run INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_task_runs_account ON task_runs(account_page_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_started ON task_runs(started_at);
"""


@dataclass
class RunRecord:
    account_page_id: str
    account_name: str
    task_name: str
    started_at: str
    completed_at: str | None = None
    status: str = "success"
    confidence: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    search_count: int = 0
    duration_seconds: float | None = None
    error: str | None = None
    output_json: str | None = None
    dry_run: bool = False


class RunLog:
    def __init__(self, path: str | Path = "runs.db"):
        self.path = Path(path)
        with self._conn() as c:
            c.executescript(_RUN_LOG_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def record(self, run: RunRecord) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO task_runs
                (account_page_id, account_name, task_name, started_at, completed_at,
                 status, confidence, model, input_tokens, output_tokens,
                 search_count, duration_seconds, error, output_json, dry_run)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run.account_page_id, run.account_name, run.task_name,
                 run.started_at, run.completed_at, run.status, run.confidence,
                 run.model, run.input_tokens, run.output_tokens, run.search_count,
                 run.duration_seconds, run.error, run.output_json,
                 1 if run.dry_run else 0),
            )
            return cur.lastrowid

    def summary(self) -> dict[str, Any]:
        with self._conn() as c:
            row = c.execute(
                """SELECT COUNT(*),
                          SUM(CASE WHEN status='success' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END),
                          SUM(input_tokens), SUM(output_tokens),
                          SUM(search_count), SUM(duration_seconds)
                   FROM task_runs"""
            ).fetchone()
        return {
            "runs": row[0] or 0, "success": row[1] or 0, "failed": row[2] or 0,
            "input_tokens": row[3] or 0, "output_tokens": row[4] or 0,
            "searches": row[5] or 0, "seconds": row[6] or 0.0,
        }


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================================
# 4. WEB SEARCH TOOL — Tavily wrapper that tracks search count per task
# ============================================================================

WEB_SEARCH_SCHEMA = {
    "name": "web_search",
    "description": (
        "Search the web for information about a company. "
        "Use this to find facts about what the company does, its industry, size, "
        "headquarters, recent news, hiring activity, and competitors. "
        "Make multiple searches with different queries to gather comprehensive info."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A specific, targeted search query."},
        },
        "required": ["query"],
    },
}


@dataclass
class WebSearchTool:
    """Callable Tavily search wrapper. Each task gets its own instance so
    `count` is scoped per-task (used for cost tracking and the search cap)."""
    client: TavilyClient = field(default_factory=lambda: TavilyClient(api_key=TAVILY_API_KEY))
    count: int = 0

    def __call__(self, query: str) -> str:
        self.count += 1
        response = self.client.search(query=query, search_depth="basic", max_results=5)
        results = response.get("results", [])
        if not results:
            return f"No results for: {query}"
        lines = []
        for i, r in enumerate(results, start=1):
            lines.append(
                f"Result {i}:\n"
                f"Title: {r.get('title', 'No title')}\n"
                f"URL: {r.get('url', 'No url')}\n"
                f"Content: {r.get('content', 'No content')}"
            )
        return "\n\n".join(lines)


# ============================================================================
# 5. TASK FRAMEWORK — generic ReAct loop. One Task = one focused research question.
# ============================================================================

@dataclass
class TaskResult:
    task_name: str
    output: dict[str, Any] | None       # parsed JSON from the agent
    confidence: str                     # "high" | "medium" | "low" | "failed"
    fields: dict[str, Any]              # Notion property updates this task wants applied
    page_blocks: list[dict[str, Any]]   # Notion blocks to append to the page body
    sources: list[str]
    search_count: int
    input_tokens: int
    output_tokens: int
    duration_seconds: float
    error: str | None = None


class Task:
    """A single research question. Subclasses set:
      - name:           short identifier (used in CLI + run log)
      - system_prompt:  instructions to the model (PLACEHOLDER for MVP)

    Subclasses may override:
      - to_fields(output): map model output -> Notion property update payload
      - to_blocks(output): map model output -> Notion children blocks for the page body
    """
    name: str = ""
    system_prompt: str = ""

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        return {}

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def run(self, account_name: str, anthropic_client: Anthropic) -> TaskResult:
        # The Anthropic SDK retries 429/5xx/529 internally; client is configured
        # with max_retries=8 in the orchestrator so transient overload absorbs.
        web_search = WebSearchTool()
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": f"Research the company: {account_name}"},
        ]
        input_tokens = 0
        output_tokens = 0
        start = time.time()

        try:
            for _ in range(MAX_AGENT_ITERATIONS):
                response = anthropic_client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=self.system_prompt,
                    tools=[WEB_SEARCH_SCHEMA],
                    messages=messages,
                )
                input_tokens += response.usage.input_tokens
                output_tokens += response.usage.output_tokens
                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason == "end_turn":
                    text = "".join(b.text for b in response.content if b.type == "text")
                    output = _extract_json(text)
                    if output is None:
                        return self._fail(f"end_turn without JSON block:\n{text}",
                                          web_search.count, input_tokens, output_tokens, start)
                    confidence = output.get("confidence", "low")
                    if confidence not in {"high", "medium", "low"}:
                        confidence = "low"
                    return TaskResult(
                        task_name=self.name, output=output, confidence=confidence,
                        fields=self.to_fields(output), page_blocks=self.to_blocks(output),
                        sources=output.get("sources", []) or [],
                        search_count=web_search.count,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        duration_seconds=time.time() - start,
                    )

                if response.stop_reason == "tool_use":
                    if web_search.count >= TAVILY_SEARCHES_PER_TASK_CAP:
                        return self._fail(f"hit search cap ({TAVILY_SEARCHES_PER_TASK_CAP})",
                                          web_search.count, input_tokens, output_tokens, start)
                    tool_results = []
                    for block in response.content:
                        if block.type != "tool_use":
                            continue
                        result = (web_search(block.input["query"])
                                  if block.name == "web_search"
                                  else f"Unknown tool: {block.name}")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                    messages.append({"role": "user", "content": tool_results})
                    continue

                return self._fail(f"Unexpected stop_reason: {response.stop_reason}",
                                  web_search.count, input_tokens, output_tokens, start)

            return self._fail(f"hit MAX_AGENT_ITERATIONS ({MAX_AGENT_ITERATIONS})",
                              web_search.count, input_tokens, output_tokens, start)
        except Exception as e:
            return self._fail(f"{type(e).__name__}: {e}",
                              web_search.count, input_tokens, output_tokens, start)

    def _fail(self, error: str, searches: int, in_tok: int, out_tok: int, start: float) -> TaskResult:
        return TaskResult(
            task_name=self.name, output=None, confidence="failed",
            fields={}, page_blocks=[], sources=[],
            search_count=searches, input_tokens=in_tok, output_tokens=out_tok,
            duration_seconds=time.time() - start, error=error,
        )


def _extract_json(text: str) -> dict[str, Any] | None:
    fenced = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            return None
    for match in re.finditer(r"\{.*?\}", text, re.DOTALL):
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
    return None


# ============================================================================
# 6. CONCRETE TASKS — placeholders for now; real Superside-specific prompts later
# ============================================================================

_COMPANY_OVERVIEW_PROMPT = """You are a B2B sales research agent. Your job is to research a company
and produce a brief profile. (Placeholder prompt — will be replaced with the
Superside-specific version later.)

You have access to a web_search tool. Make 2-5 targeted searches before producing
your final answer.

When you have enough information, output your final answer as a single JSON object
inside a ```json code block. The JSON object must have exactly these fields:

{
  "company_name": string,
  "summary": string,                              // 2-3 sentence company summary
  "industry": string,
  "size_band": "<1000" | "1000-2000" | "2000-5000" | "5000+" | null,
  "hq_country": string | null,
  "is_hiring_creatives": boolean | null,          // placeholder hook
  "sources": [string, ...],
  "confidence": "high" | "medium" | "low",
  "notes": string
}

Rules:
- Base every claim on what your search results actually say.
- "sources" must be URLs from your search results.
- "confidence": "high" if multiple authoritative sources agree, "medium" if sparse
  or mixed, "low" if you had to guess.
- If a field can't be determined, use null and lower confidence.
"""


class CompanyOverview(Task):
    name = "company_overview"
    system_prompt = _COMPANY_OVERVIEW_PROMPT

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        size = output.get("size_band")
        if size in SIZE_OPTIONS:
            fields[PROP_SIZE] = {"select": {"name": size}}
        if output.get("is_hiring_creatives"):
            fields[PROP_BUYING_SIGNALS] = {"multi_select": [{"name": "hiring"}]}
        return fields

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if output.get("summary"):
            blocks.append(_paragraph(output["summary"]))
        for label, key in [
            ("Industry", "industry"),
            ("Size", "size_band"),
            ("HQ", "hq_country"),
            ("Hiring creatives", "is_hiring_creatives"),
        ]:
            value = output.get(key)
            if value is None or value == "":
                continue
            blocks.append(_bullet(f"{label}: {value}"))
        if output.get("notes"):
            blocks.append(_bullet(f"Notes: {output['notes']}"))
        return blocks


# Register every Task subclass here so the CLI can resolve names.
TASK_REGISTRY: dict[str, type[Task]] = {
    "company_overview": CompanyOverview,
}


# ============================================================================
# 7. NOTION CRM CLIENT — list, update, append blocks
# ============================================================================

@dataclass
class Account:
    page_id: str
    name: str
    rep: str | None
    priority_type: str | None
    last_researched: str | None  # ISO date string


class NotionCRM:
    def __init__(self, token: str | None = None, database_id: str | None = None):
        self.client = NotionClient(auth=token or NOTION_API_KEY)
        self.database_id = database_id or NOTION_DATABASE_ID

    def list_accounts(
        self,
        rep: str | None = None,
        priority_type: str | None = None,
        researched_before: str | None = None,
        limit: int | None = None,
    ) -> list[Account]:
        and_filters: list[dict[str, Any]] = []
        if rep:
            and_filters.append({"property": PROP_REP, "select": {"equals": rep}})
        if priority_type:
            and_filters.append({"property": PROP_PRIORITY_TYPE, "select": {"equals": priority_type}})
        if researched_before:
            and_filters.append({
                "or": [
                    {"property": PROP_LAST_RESEARCHED, "date": {"is_empty": True}},
                    {"property": PROP_LAST_RESEARCHED, "date": {"before": researched_before}},
                ]
            })

        query: dict[str, Any] = {"database_id": self.database_id}
        if and_filters:
            query["filter"] = {"and": and_filters} if len(and_filters) > 1 else and_filters[0]

        results: list[Account] = []
        cursor = None
        while True:
            if cursor:
                query["start_cursor"] = cursor
            response = self.client.databases.query(**query)
            for page in response.get("results", []):
                results.append(_account_from_page(page))
                if limit and len(results) >= limit:
                    return results
            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")
        return results

    def update_properties(self, page_id: str, properties: dict[str, Any]) -> None:
        self.client.pages.update(page_id=page_id, properties=properties)

    def append_blocks(self, page_id: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        # Notion API caps appends at 100 blocks per call.
        for i in range(0, len(blocks), 100):
            self.client.blocks.children.append(block_id=page_id, children=blocks[i:i + 100])


def _account_from_page(page: dict[str, Any]) -> Account:
    props = page.get("properties", {})
    return Account(
        page_id=page["id"],
        name=_extract_title(props.get(PROP_ACCOUNT_NAME)),
        rep=_extract_select(props.get(PROP_REP)),
        priority_type=_extract_select(props.get(PROP_PRIORITY_TYPE)),
        last_researched=_extract_date(props.get(PROP_LAST_RESEARCHED)),
    )


def _extract_title(prop: dict[str, Any] | None) -> str:
    if not prop:
        return ""
    return "".join(p.get("plain_text", "") for p in prop.get("title", [])).strip()


def _extract_select(prop: dict[str, Any] | None) -> str | None:
    if not prop:
        return None
    sel = prop.get("select")
    return sel.get("name") if sel else None


def _extract_date(prop: dict[str, Any] | None) -> str | None:
    if not prop:
        return None
    d = prop.get("date")
    return d.get("start") if d else None


# Notion block helpers used by tasks AND the orchestrator's research-section header.
def _paragraph(text: str) -> dict[str, Any]:
    return {
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _bullet(text: str) -> dict[str, Any]:
    return {
        "object": "block", "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


# ============================================================================
# 8. ORCHESTRATOR — for each account, run each task, write back to Notion + log to SQLite
# ============================================================================

# Confidence ranking: lower = better. Used to aggregate per-task confidences.
_CONF_RANK = {"high": 0, "medium": 1, "low": 2, "failed": 3}


@dataclass
class AccountOutcome:
    account: Account
    task_results: list[TaskResult]
    overall_confidence: str
    overall_status: str          # done | needs_review | failed
    wrote_to_notion: bool
    error: str | None = None


class Orchestrator:
    def __init__(self, crm: NotionCRM, run_log: RunLog,
                 anthropic_client: Anthropic | None = None,
                 concurrency: int = DEFAULT_CONCURRENCY):
        self.crm = crm
        self.run_log = run_log
        self.anthropic = anthropic_client or Anthropic(
            api_key=ANTHROPIC_API_KEY,
            max_retries=8,  # absorb transient 529 / 5xx during peak load
        )
        self.concurrency = concurrency

    def run(self, accounts: list[Account], task_names: list[str],
            dry_run: bool = False) -> list[AccountOutcome]:
        unknown = [n for n in task_names if n not in TASK_REGISTRY]
        if unknown:
            raise ValueError(f"Unknown task(s): {unknown}. Known: {list(TASK_REGISTRY)}")
        tasks = [TASK_REGISTRY[n]() for n in task_names]

        outcomes: list[AccountOutcome] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(self._process_account, a, tasks, dry_run): a for a in accounts}
            for fut in as_completed(futures):
                acc = futures[fut]
                try:
                    outcome = fut.result()
                except Exception as e:
                    logging.exception("[%s] account failed", acc.name)
                    outcome = AccountOutcome(
                        account=acc, task_results=[], overall_confidence="failed",
                        overall_status="failed", wrote_to_notion=False, error=str(e),
                    )
                outcomes.append(outcome)
                ok = sum(1 for r in outcome.task_results if r.error is None)
                logging.info(
                    "[%s] %s | conf=%s | %d/%d ok | %s",
                    outcome.account.name, outcome.overall_status,
                    outcome.overall_confidence, ok, len(outcome.task_results),
                    "WROTE" if outcome.wrote_to_notion else "no-write",
                )
        return outcomes

    def _process_account(self, account: Account, tasks: list[Task],
                         dry_run: bool) -> AccountOutcome:
        results: list[TaskResult] = []
        for task in tasks:
            logging.info("[%s] %s — running…", account.name, task.name)
            result = task.run(account.name, anthropic_client=self.anthropic)
            results.append(result)
            self.run_log.record(RunRecord(
                account_page_id=account.page_id, account_name=account.name,
                task_name=result.task_name,
                started_at=_iso_now(), completed_at=_iso_now(),
                status="failed" if result.error else ("dry_run" if dry_run else "success"),
                confidence=result.confidence, model=MODEL,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                search_count=result.search_count, duration_seconds=result.duration_seconds,
                error=result.error,
                output_json=json.dumps(result.output, ensure_ascii=False) if result.output else None,
                dry_run=dry_run,
            ))

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
            logging.exception("[%s] Notion write failed", account.name)
            return AccountOutcome(
                account=account, task_results=results, overall_confidence=overall_conf,
                overall_status="failed", wrote_to_notion=False, error=str(e),
            )

        return AccountOutcome(
            account=account, task_results=results, overall_confidence=overall_conf,
            overall_status=overall_status, wrote_to_notion=True,
        )

    def _write_to_notion(self, account: Account, results: list[TaskResult],
                         overall_conf: str, overall_status: str) -> None:
        # Merge per-task field updates. Multi-selects are unioned, not overwritten.
        properties: dict[str, Any] = {}
        for r in results:
            for k, v in r.fields.items():
                if k == PROP_BUYING_SIGNALS and k in properties:
                    existing = properties[k]["multi_select"]
                    seen = {item["name"] for item in existing}
                    for item in v["multi_select"]:
                        if item["name"] not in seen:
                            existing.append(item)
                            seen.add(item["name"])
                else:
                    properties[k] = v

        # Always write agent-managed metadata.
        properties[PROP_LAST_RESEARCHED] = {"date": {"start": date.today().isoformat()}}
        properties[PROP_RESEARCH_CONFIDENCE] = {
            "select": {"name": overall_conf if overall_conf != "failed" else "low"}
        }
        properties[PROP_RESEARCH_STATUS] = {"select": {"name": overall_status}}

        self.crm.update_properties(account.page_id, properties)

        # Append free-form research blocks to the page body.
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
    today = date.today().isoformat()
    blocks: list[dict[str, Any]] = [{
        "object": "block", "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text",
                                     "text": {"content": f"Research — {today}"}}]},
    }]
    for r in results:
        blocks.append({
            "object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": r.task_name}}]},
        })
        if r.error:
            blocks.append(_paragraph(f"Failed: {r.error}"))
            continue
        blocks.extend(r.page_blocks)
        if r.sources:
            blocks.append(_paragraph("Sources:"))
            for url in r.sources:
                blocks.append(_bullet(url))
    blocks.append({"object": "block", "type": "divider", "divider": {}})
    return blocks


# ============================================================================
# 9. CLI / MAIN — argparse + the entry point
# ============================================================================

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Research account profiles via Claude + Tavily.")
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
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--db", default="runs.db", help="SQLite run-log path")
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

    print(f"Matched {len(accounts)} account(s):")
    for a in accounts:
        print(f"  - {a.name}")
    print(f"Tasks: {args.tasks}")
    print(f"Mode: {'DRY-RUN (no Notion writes)' if args.dry_run else 'LIVE (Notion writes enabled)'}")
    print(f"Concurrency: {args.concurrency}\n")

    task_names = [t.strip() for t in args.tasks.split(",") if t.strip()]
    orch = Orchestrator(crm=crm, run_log=run_log, concurrency=args.concurrency)
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
