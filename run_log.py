"""SQLite run log: one row per (account, task, run).

Records prompt_version, provider, and git_sha alongside the usual stats so we can
answer "did this prompt version perform better than the last one?" later.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


_SCHEMA = """
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
    dry_run INTEGER DEFAULT 0,
    prompt_version TEXT,                   -- added Phase 1
    provider TEXT,                         -- added Phase 1
    git_sha TEXT,                          -- added Phase 1
    cached_input_tokens INTEGER DEFAULT 0, -- added Phase 1.5a
    model_tier TEXT,                       -- added Phase 1.5a — "smart" | "fast"
    model_used TEXT,                       -- added Phase 1.5a — actual model name
    tool_results_seen TEXT                 -- Fix #6 — JSON list of URLs the model actually saw
);
CREATE INDEX IF NOT EXISTS idx_task_runs_account ON task_runs(account_page_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_started ON task_runs(started_at);
-- Phase A freshness gating (2026-05-13): pre-load "last successful run per
-- (account, task)" in one GROUP BY query so the orchestrator can skip
-- modules whose output is still fresh per its cadence threshold.
CREATE INDEX IF NOT EXISTS idx_task_runs_freshness
    ON task_runs(account_page_id, task_name, started_at DESC);

CREATE TABLE IF NOT EXISTS tool_call_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    response_text TEXT NOT NULL,
    cached_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE(tool_name, args_hash)
);
CREATE INDEX IF NOT EXISTS idx_tool_cache_lookup
    ON tool_call_cache(tool_name, args_hash, expires_at);

-- Phase 2c (2026-05-11): one row per ATS fetch. ATSFetcherTool diffs the
-- latest snapshot for a given (company, provider) against the current scrape
-- to surface roles that closed between runs — closures of marketing/creative
-- roles are buying signals too.
CREATE TABLE IF NOT EXISTS ats_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    slug TEXT,
    snapshot_at TEXT NOT NULL,
    titles_json TEXT NOT NULL,    -- JSON array of role titles (canonical strings)
    jobs_json TEXT NOT NULL       -- full role records for richer follow-up diffs
);
CREATE INDEX IF NOT EXISTS idx_ats_snapshots_lookup
    ON ats_snapshots(company_name, provider, snapshot_at DESC);

-- Phase C (2026-05-13): dedup ledger for Notion alerts. The orchestrator
-- writes one row per fired DetectedEvent so subsequent runs within the
-- cooldown window (default 14 days) can suppress re-alerting for the same
-- (account, signature). Cleared implicitly when the human sets
-- `Attention Acknowledged At` on the Notion page — see crm.get_attention_acknowledged_at.
CREATE TABLE IF NOT EXISTS event_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_page_id TEXT NOT NULL,
    signature TEXT NOT NULL,
    alerted_at TEXT NOT NULL,
    module TEXT,
    signal_type TEXT,
    summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_alerts_lookup
    ON event_alerts(account_page_id, signature, alerted_at DESC);
"""

# Columns added in Phase 1 — old DBs need ALTER TABLE migration.
_PHASE1_COLS = ["prompt_version", "provider", "git_sha"]
# Columns added in Phase 1.5a.
_PHASE15A_COLS_TEXT = ["model_tier", "model_used"]
_PHASE15A_COLS_INT = ["cached_input_tokens"]
# Column added by Fix Appendix #6 — JSON list of URLs the model actually saw via tools.
_FIXAPP_COLS_TEXT = ["tool_results_seen"]


def _open_conn(path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection hardened for our ThreadPoolExecutor write pattern.

    PRAGMAs (Fix Appendix #7):
      - journal_mode=WAL  — readers and writers don't block each other
      - busy_timeout=30s  — wait instead of failing on transient lock contention
      - foreign_keys=ON   — enforce FK constraints if any tables ever add them
    """
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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
    prompt_version: str | None = None
    provider: str | None = None
    git_sha: str | None = None
    cached_input_tokens: int = 0
    model_tier: str | None = None
    model_used: str | None = None
    tool_results_seen: str | None = None  # JSON list of URLs the model actually saw


class RunLog:
    def __init__(self, path: str | Path = "runs.db"):
        self.path = Path(path)
        with self._conn() as c:
            c.executescript(_SCHEMA)
            self._migrate(c)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Idempotently add Phase 1 + 1.5a + Fix Appendix columns to pre-existing databases."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(task_runs)")}
        for col in _PHASE1_COLS:
            if col not in existing:
                conn.execute(f"ALTER TABLE task_runs ADD COLUMN {col} TEXT")
        for col in _PHASE15A_COLS_TEXT:
            if col not in existing:
                conn.execute(f"ALTER TABLE task_runs ADD COLUMN {col} TEXT")
        for col in _PHASE15A_COLS_INT:
            if col not in existing:
                conn.execute(f"ALTER TABLE task_runs ADD COLUMN {col} INTEGER DEFAULT 0")
        for col in _FIXAPP_COLS_TEXT:
            if col not in existing:
                conn.execute(f"ALTER TABLE task_runs ADD COLUMN {col} TEXT")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = _open_conn(self.path)
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
                 search_count, duration_seconds, error, output_json, dry_run,
                 prompt_version, provider, git_sha,
                 cached_input_tokens, model_tier, model_used, tool_results_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run.account_page_id, run.account_name, run.task_name,
                 run.started_at, run.completed_at, run.status, run.confidence,
                 run.model, run.input_tokens, run.output_tokens, run.search_count,
                 run.duration_seconds, run.error, run.output_json,
                 1 if run.dry_run else 0,
                 run.prompt_version, run.provider, run.git_sha,
                 run.cached_input_tokens, run.model_tier, run.model_used,
                 run.tool_results_seen),
            )
            return cur.lastrowid

    def latest_successful_runs(
        self, account_page_ids: list[str],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Pre-load the most recent USABLE task_run per (account, task) for the
        given accounts. One GROUP BY query, used by Phase A freshness gating.

        "Usable" excludes:
          - status != 'success'      (errored / dry-run rows)
          - confidence == 'failed'   (model self-reported it couldn't do the work
                                      — this is the subtle case: the row may
                                      log status='success' but the agent has
                                      no usable signal to surface, so the
                                      output is NOT fresh for cadence purposes)
          - dry_run = 1              (rehearsals don't count as fresh)

        Returns `{(account_page_id, task_name): {started_at, output_json,
        prompt_version, confidence}}`. Empty dict if no inputs.
        """
        if not account_page_ids:
            return {}
        placeholders = ",".join("?" for _ in account_page_ids)
        with self._conn() as c:
            rows = c.execute(
                f"""SELECT account_page_id, task_name,
                          MAX(started_at) AS latest,
                          output_json, prompt_version, confidence
                   FROM task_runs
                   WHERE account_page_id IN ({placeholders})
                     AND status = 'success'
                     AND (confidence IS NULL OR confidence != 'failed')
                     AND COALESCE(dry_run, 0) = 0
                   GROUP BY account_page_id, task_name""",
                account_page_ids,
            ).fetchall()
        return {
            (pid, task): {
                "started_at": started_at,
                "output_json": output_json,
                "prompt_version": prompt_version,
                "confidence": confidence,
            }
            for (pid, task, started_at, output_json, prompt_version, confidence) in rows
        }

    # ---- Phase C: alert dedup ledger ----

    def recent_alert(
        self,
        page_id: str,
        signature: str,
        *,
        within_days: int = 14,
    ) -> str | None:
        """Return the most recent alert timestamp for (page_id, signature)
        within the cooldown window, or None if no recent alert exists.

        Used by the orchestrator before passing DetectedEvents to writeback —
        if we alerted on the same signature within 14d AND the human hasn't
        acknowledged since, skip the re-alert."""
        from datetime import datetime as _dt, timedelta, timezone as _tz
        cutoff = (_dt.now(_tz.utc) - timedelta(days=within_days)).isoformat()
        with self._conn() as c:
            row = c.execute(
                """SELECT MAX(alerted_at) FROM event_alerts
                   WHERE account_page_id = ? AND signature = ? AND alerted_at >= ?""",
                (page_id, signature, cutoff),
            ).fetchone()
        return row[0] if row and row[0] else None

    def record_alert(
        self,
        page_id: str,
        signature: str,
        *,
        module: str | None = None,
        signal_type: str | None = None,
        summary: str | None = None,
    ) -> None:
        """Append a row to the event_alerts ledger. Phase C calls this when
        the orchestrator hands an event to writeback (i.e. AFTER dedup, so
        every recorded row represents a real Notion-side action)."""
        with self._conn() as c:
            c.execute(
                """INSERT INTO event_alerts
                   (account_page_id, signature, alerted_at, module, signal_type, summary)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (page_id, signature, iso_now(), module, signal_type, summary),
            )

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

    def cost_summary(
        self,
        *,
        since: str | None = None,
        account_name: str | None = None,
        git_sha: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate USD cost from token + search counters, broken down by
        per-model, per-task, and per-account. Filter optionally by:

        - since:        ISO timestamp; only rows with started_at >= this
        - account_name: exact match on the account_name column
        - git_sha:      exact match on the git_sha column (useful for "what
                        did this build cost?")

        Cost computation:
        - LLM cost via config.estimate_usd_cost — uses per-model pricing,
          counts cached_input_tokens separately. Falls back to the `model`
          column when `model_used` is null (older rows pre-Phase 1.5a).
        - Tavily search cost: search_count × TAVILY_COST_PER_SEARCH.
        - Apify cost is NOT estimated — accumulated on Apify dashboard.

        Returns a dict with totals + three breakdown lists, each sorted by $
        descending. Suitable for direct rendering by the CLI or cost_report.py.
        """
        import config

        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("started_at >= ?")
            params.append(since)
        if account_name is not None:
            clauses.append("account_name = ?")
            params.append(account_name)
        if git_sha is not None:
            clauses.append("git_sha = ?")
            params.append(git_sha)
        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        with self._conn() as c:
            rows = c.execute(
                f"""SELECT account_name, task_name,
                          COALESCE(model_used, model) AS model_resolved,
                          status,
                          COALESCE(input_tokens, 0),
                          COALESCE(output_tokens, 0),
                          COALESCE(cached_input_tokens, 0),
                          COALESCE(search_count, 0),
                          COALESCE(error, ''),
                          COALESCE(output_json, '')
                   FROM task_runs{where_sql}""",
                params,
            ).fetchall()

        per_account: dict[str, float] = {}
        per_task: dict[str, dict[str, Any]] = {}
        per_model: dict[str, dict[str, Any]] = {}
        total_llm_usd = 0.0
        total_search_usd = 0.0
        total_input = 0
        total_output = 0
        total_cached_input = 0
        total_searches = 0
        total_rows = 0
        failed_rows = 0
        tavily_quota_affected = 0
        successful_account_pairs: set[tuple[str, str]] = set()

        for (acct, task, model, status, in_tok, out_tok, cached_tok, searches, err, output_json) in rows:
            total_rows += 1
            llm_usd = config.estimate_usd_cost(
                in_tok, out_tok, model, cached_input_tokens=cached_tok,
            )
            search_usd = (searches or 0) * config.TAVILY_COST_PER_SEARCH
            row_usd = llm_usd + search_usd

            total_llm_usd += llm_usd
            total_search_usd += search_usd
            total_input += in_tok
            total_output += out_tok
            total_cached_input += cached_tok
            total_searches += searches
            if status == "success":
                successful_account_pairs.add((acct, task))
            elif status == "failed":
                failed_rows += 1
            # Tavily quota detection. The breaker's short-circuit string is
            # "ERROR: Tavily monthly quota exhausted (HTTP 432)" — when the
            # model saw it in a tool_result and kept going, the row is
            # status='success' but degraded; when the model couldn't recover,
            # the row is status='failed' with the same string in `error`.
            # Scan both `error` and `output_json` to catch both cases.
            haystack = ((err or "") + " " + (output_json or "")).lower()
            if "tavily" in haystack and ("432" in haystack or "quota" in haystack):
                tavily_quota_affected += 1

            per_account[acct] = per_account.get(acct, 0.0) + row_usd

            t = per_task.setdefault(task, {
                "task": task, "usd": 0.0, "input_tokens": 0,
                "output_tokens": 0, "cached_input_tokens": 0,
                "searches": 0, "rows": 0,
            })
            t["usd"] += row_usd
            t["input_tokens"] += in_tok
            t["output_tokens"] += out_tok
            t["cached_input_tokens"] += cached_tok
            t["searches"] += searches
            t["rows"] += 1

            m_key = model or "(unknown)"
            m = per_model.setdefault(m_key, {
                "model": m_key, "usd": 0.0, "input_tokens": 0,
                "output_tokens": 0, "cached_input_tokens": 0, "rows": 0,
            })
            m["usd"] += llm_usd
            m["input_tokens"] += in_tok
            m["output_tokens"] += out_tok
            m["cached_input_tokens"] += cached_tok
            m["rows"] += 1

        account_count = len(per_account)
        per_account_list = sorted(
            [{"account": a, "usd": v} for a, v in per_account.items()],
            key=lambda x: x["usd"], reverse=True,
        )
        per_task_list = sorted(
            list(per_task.values()), key=lambda x: x["usd"], reverse=True,
        )
        per_model_list = sorted(
            list(per_model.values()), key=lambda x: x["usd"], reverse=True,
        )

        total_usd = total_llm_usd + total_search_usd

        return {
            "total_usd": total_usd,
            "llm_usd": total_llm_usd,
            "search_usd": total_search_usd,
            "rows": total_rows,
            "failed_rows": failed_rows,
            "tavily_quota_affected": tavily_quota_affected,
            "accounts": account_count,
            "avg_per_account_usd": (total_usd / account_count) if account_count else 0.0,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cached_input_tokens": total_cached_input,
            "searches": total_searches,
            "per_account": per_account_list,
            "per_task": per_task_list,
            "per_model": per_model_list,
        }


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ToolCallCache:
    """24h cache for tool results, keyed by (tool_name, args_hash).

    Lives in the same SQLite file as task_runs so the run log + tool cache stay
    co-located for a given monthly batch. Hits/misses logged via cache_lookup().
    """

    def __init__(self, path: str | Path = "runs.db", ttl_hours: int = 24):
        from datetime import timedelta
        self.path = Path(path)
        self.ttl = timedelta(hours=ttl_hours)
        # Schema lives in RunLog._SCHEMA — just open the connection.
        with _open_conn(self.path) as c:
            c.executescript(_SCHEMA)

    @staticmethod
    def _hash_args(args: dict[str, Any]) -> str:
        import hashlib
        normalized = json.dumps(args, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(normalized.encode()).hexdigest()[:32]

    def lookup(self, tool_name: str, args: dict[str, Any]) -> str | None:
        """Return cached response text if not expired, else None."""
        args_hash = self._hash_args(args)
        with _open_conn(self.path) as c:
            row = c.execute(
                """SELECT response_text, expires_at
                   FROM tool_call_cache
                   WHERE tool_name = ? AND args_hash = ?""",
                (tool_name, args_hash),
            ).fetchone()
        if row is None:
            return None
        response_text, expires_at = row
        if expires_at and expires_at < iso_now():
            return None  # expired — caller will re-fetch and overwrite
        return response_text

    def store(
        self,
        tool_name: str,
        args: dict[str, Any],
        response_text: str,
        ttl_hours: float | None = None,
        ttl_minutes: float | None = None,
    ) -> None:
        """Cache a response. Overrides instance-level TTL when caller passes one.

        Fix Appendix #9: a 24h cache for "ERROR: retryable search failure: 429"
        poisons every subsequent call for a day. Tools pass `ttl_minutes=30` for
        retryable errors (or skip caching entirely) and the default 24h only for
        true successes.
        """
        from datetime import datetime as _dt, timedelta, timezone as _tz
        args_hash = self._hash_args(args)
        cached_at = iso_now()
        if ttl_hours is not None:
            ttl = timedelta(hours=ttl_hours)
        elif ttl_minutes is not None:
            ttl = timedelta(minutes=ttl_minutes)
        else:
            ttl = self.ttl
        expires_at = (_dt.now(_tz.utc) + ttl).isoformat()
        with _open_conn(self.path) as c:
            c.execute(
                """INSERT OR REPLACE INTO tool_call_cache
                   (tool_name, args_hash, response_text, cached_at, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (tool_name, args_hash, response_text, cached_at, expires_at),
            )

    def stats(self) -> dict[str, int]:
        """Return current cache size + expired count for telemetry."""
        with _open_conn(self.path) as c:
            total = c.execute("SELECT COUNT(*) FROM tool_call_cache").fetchone()[0]
            now = iso_now()
            fresh = c.execute(
                "SELECT COUNT(*) FROM tool_call_cache WHERE expires_at > ?",
                (now,),
            ).fetchone()[0]
        return {"total": total, "fresh": fresh, "expired": total - fresh}


class ATSSnapshotStore:
    """Persistent store for ATS open-role snapshots, keyed by (company, provider).

    Each fetch appends a new row; `load_latest` returns the most recent prior
    snapshot so the ATSFetcherTool can diff and surface roles that closed
    between runs (a closure of an important marketing/creative role is a
    buying signal — see tools/ats_fetcher.py).
    """

    def __init__(self, path: str | Path = "runs.db"):
        self.path = Path(path)
        with _open_conn(self.path) as c:
            c.executescript(_SCHEMA)

    def store(
        self,
        *,
        company: str,
        provider: str,
        slug: str | None,
        jobs: list[dict[str, Any]],
    ) -> None:
        titles = [j.get("title", "") for j in jobs if j.get("title")]
        with _open_conn(self.path) as c:
            c.execute(
                """INSERT INTO ats_snapshots
                   (company_name, provider, slug, snapshot_at, titles_json, jobs_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (company, provider, slug, iso_now(),
                 json.dumps(titles, ensure_ascii=False),
                 json.dumps(jobs, ensure_ascii=False, default=str)),
            )

    def load_latest(
        self, *, company: str, provider: str,
    ) -> dict[str, Any] | None:
        """Return the most recent snapshot for the (company, provider) pair, or
        None if no prior snapshot exists. Decodes `titles` back to a Python list."""
        with _open_conn(self.path) as c:
            row = c.execute(
                """SELECT id, slug, snapshot_at, titles_json, jobs_json
                   FROM ats_snapshots
                   WHERE company_name = ? AND provider = ?
                   ORDER BY id DESC LIMIT 1""",
                (company, provider),
            ).fetchone()
        if row is None:
            return None
        _id, slug, snapshot_at, titles_json, jobs_json = row
        try:
            titles = json.loads(titles_json) if titles_json else []
        except json.JSONDecodeError:
            titles = []
        try:
            jobs = json.loads(jobs_json) if jobs_json else []
        except json.JSONDecodeError:
            jobs = []
        return {
            "slug": slug,
            "snapshot_at": snapshot_at,
            "titles": titles,
            "jobs": jobs,
        }


def serialize_output(output: Any) -> str | None:
    if output is None:
        return None
    return json.dumps(output, ensure_ascii=False)
