"""Tests for cost computation + RunLog.cost_summary() aggregation."""

from __future__ import annotations

import pytest

import config
from run_log import RunLog, RunRecord, iso_now


# ---- config.estimate_usd_cost ----

def test_estimate_usd_cost_known_model_matches_pricing_table() -> None:
    """1M input + 1M output on Sonnet 4.6 should equal $3 + $15 = $18."""
    cost = config.estimate_usd_cost(1_000_000, 1_000_000, "claude-sonnet-4-6")
    assert cost == pytest.approx(18.00, abs=0.001)


def test_estimate_usd_cost_cached_input_billed_at_cached_rate() -> None:
    """500K fresh input + 500K cached input + 0 output on Sonnet:
    500K * 3.00/M + 500K * 0.30/M = $1.50 + $0.15 = $1.65"""
    cost = config.estimate_usd_cost(
        1_000_000, 0, "claude-sonnet-4-6", cached_input_tokens=500_000,
    )
    assert cost == pytest.approx(1.65, abs=0.001)


def test_estimate_usd_cost_unknown_model_returns_zero() -> None:
    cost = config.estimate_usd_cost(1_000_000, 1_000_000, "some-unknown-model")
    assert cost == 0.0


def test_estimate_usd_cost_none_model_returns_zero() -> None:
    cost = config.estimate_usd_cost(1_000_000, 1_000_000, None)
    assert cost == 0.0


def test_estimate_usd_cost_zero_tokens_returns_zero() -> None:
    cost = config.estimate_usd_cost(0, 0, "claude-sonnet-4-6")
    assert cost == 0.0


def test_tavily_cost_per_search_constant_present() -> None:
    """Cost summary depends on this constant — make sure it stays > 0 and
    matches Tavily's public per-search price."""
    assert config.TAVILY_COST_PER_SEARCH > 0
    assert config.TAVILY_COST_PER_SEARCH == pytest.approx(0.008, abs=0.001)


# ---- RunLog.cost_summary ----

def _record(
    rl: RunLog,
    *,
    account: str,
    task: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    searches: int = 0,
    status: str = "success",
    git_sha: str | None = None,
) -> None:
    rl.record(RunRecord(
        account_page_id=f"pid-{account}",
        account_name=account,
        task_name=task,
        started_at=iso_now(),
        completed_at=iso_now(),
        status=status,
        confidence="high",
        model=model,
        model_used=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        search_count=searches,
        duration_seconds=1.0,
        git_sha=git_sha,
    ))


def test_cost_summary_empty_db_returns_zeroed_dict(tmp_path) -> None:
    rl = RunLog(tmp_path / "runs.db")
    cs = rl.cost_summary()
    assert cs["total_usd"] == 0.0
    assert cs["rows"] == 0
    assert cs["accounts"] == 0
    assert cs["per_account"] == []
    assert cs["per_task"] == []
    assert cs["per_model"] == []


def test_cost_summary_totals_match_per_call_estimates(tmp_path) -> None:
    rl = RunLog(tmp_path / "runs.db")
    # Two Sonnet calls on the same account, different tasks.
    _record(rl, account="Oracle", task="module_01_gate",
            model="claude-sonnet-4-6", input_tokens=10_000, output_tokens=2_000)
    _record(rl, account="Oracle", task="module_02_revenue_model",
            model="claude-sonnet-4-6", input_tokens=8_000, output_tokens=1_500)
    cs = rl.cost_summary()

    # Hand-computed: (10K+8K input + 2K+1.5K output) on Sonnet pricing.
    expected_llm = (18_000 * 3.00 + 3_500 * 15.00) / 1_000_000
    assert cs["llm_usd"] == pytest.approx(expected_llm, abs=1e-6)
    assert cs["search_usd"] == 0.0
    assert cs["total_usd"] == pytest.approx(expected_llm, abs=1e-6)
    assert cs["rows"] == 2
    assert cs["accounts"] == 1
    assert cs["avg_per_account_usd"] == pytest.approx(expected_llm, abs=1e-6)


def test_cost_summary_tavily_search_cost_added(tmp_path) -> None:
    rl = RunLog(tmp_path / "runs.db")
    _record(rl, account="X", task="t1", model="claude-sonnet-4-6",
            input_tokens=1_000, output_tokens=100, searches=10)
    cs = rl.cost_summary()
    # 10 * $0.008 = $0.08 search cost
    assert cs["search_usd"] == pytest.approx(0.08, abs=1e-4)
    assert cs["searches"] == 10


def test_cost_summary_groups_by_account_task_and_model(tmp_path) -> None:
    rl = RunLog(tmp_path / "runs.db")
    _record(rl, account="Oracle", task="module_01_gate",
            model="claude-sonnet-4-6", input_tokens=10_000, output_tokens=2_000)
    _record(rl, account="Oracle", task="module_02_revenue_model",
            model="claude-haiku-4-5", input_tokens=8_000, output_tokens=1_500)
    _record(rl, account="AlphaSense", task="module_01_gate",
            model="claude-sonnet-4-6", input_tokens=5_000, output_tokens=1_000)
    cs = rl.cost_summary()

    # Two accounts surfaced
    accts = {r["account"] for r in cs["per_account"]}
    assert accts == {"Oracle", "AlphaSense"}
    # Two tasks surfaced
    tasks = {r["task"] for r in cs["per_task"]}
    assert tasks == {"module_01_gate", "module_02_revenue_model"}
    # Two models surfaced
    models = {r["model"] for r in cs["per_model"]}
    assert models == {"claude-sonnet-4-6", "claude-haiku-4-5"}

    # Tables sorted by $ descending.
    per_acct_costs = [r["usd"] for r in cs["per_account"]]
    assert per_acct_costs == sorted(per_acct_costs, reverse=True)
    per_task_costs = [r["usd"] for r in cs["per_task"]]
    assert per_task_costs == sorted(per_task_costs, reverse=True)
    per_model_costs = [r["usd"] for r in cs["per_model"]]
    assert per_model_costs == sorted(per_model_costs, reverse=True)


def test_cost_summary_filter_by_account(tmp_path) -> None:
    rl = RunLog(tmp_path / "runs.db")
    _record(rl, account="Oracle", task="t1",
            model="claude-sonnet-4-6", input_tokens=10_000, output_tokens=2_000)
    _record(rl, account="AlphaSense", task="t1",
            model="claude-sonnet-4-6", input_tokens=10_000, output_tokens=2_000)
    cs = rl.cost_summary(account_name="Oracle")
    assert cs["rows"] == 1
    assert cs["accounts"] == 1
    assert cs["per_account"][0]["account"] == "Oracle"


def test_cost_summary_filter_by_git_sha(tmp_path) -> None:
    rl = RunLog(tmp_path / "runs.db")
    _record(rl, account="A", task="t1",
            model="claude-sonnet-4-6", input_tokens=10_000, output_tokens=2_000,
            git_sha="aaaaaaa")
    _record(rl, account="B", task="t1",
            model="claude-sonnet-4-6", input_tokens=10_000, output_tokens=2_000,
            git_sha="bbbbbbb")
    cs = rl.cost_summary(git_sha="aaaaaaa")
    assert cs["rows"] == 1
    assert cs["per_account"][0]["account"] == "A"


def test_cost_summary_filter_by_since(tmp_path) -> None:
    """Rows started before `since` should be excluded — used by the CLI to
    isolate batch cost from lifetime cost."""
    rl = RunLog(tmp_path / "runs.db")
    # An old row, dated 2020.
    rl.record(RunRecord(
        account_page_id="pid-old", account_name="Old", task_name="t1",
        started_at="2020-01-01T00:00:00+00:00",
        completed_at="2020-01-01T00:01:00+00:00",
        status="success", confidence="high",
        model="claude-sonnet-4-6", model_used="claude-sonnet-4-6",
        input_tokens=10_000, output_tokens=2_000,
    ))
    # A new row, dated now.
    _record(rl, account="New", task="t1",
            model="claude-sonnet-4-6", input_tokens=10_000, output_tokens=2_000)
    cs = rl.cost_summary(since="2025-01-01T00:00:00+00:00")
    assert cs["rows"] == 1
    assert cs["per_account"][0]["account"] == "New"


def test_cost_summary_unknown_model_zeros_llm_cost_but_still_counts_searches(tmp_path) -> None:
    """If the model column has an unmapped value, LLM cost is 0 but Tavily
    cost still computes. This protects the report when running against an
    older runs.db with retired model names."""
    rl = RunLog(tmp_path / "runs.db")
    _record(rl, account="X", task="t1", model="legacy-vintage-model",
            input_tokens=10_000, output_tokens=2_000, searches=5)
    cs = rl.cost_summary()
    assert cs["llm_usd"] == 0.0
    assert cs["search_usd"] == pytest.approx(0.04, abs=1e-4)


def test_cost_summary_includes_failed_runs(tmp_path) -> None:
    """Failed tasks still cost money — they must show in the cost report."""
    rl = RunLog(tmp_path / "runs.db")
    _record(rl, account="X", task="t1",
            model="claude-sonnet-4-6", input_tokens=10_000, output_tokens=2_000,
            status="failed")
    cs = rl.cost_summary()
    assert cs["rows"] == 1
    assert cs["total_usd"] > 0


# ---- Failure surfacing in cost_summary ----

def _record_failed(
    rl: RunLog,
    *,
    account: str,
    task: str,
    error: str,
    model: str = "claude-sonnet-4-6",
) -> None:
    rl.record(RunRecord(
        account_page_id=f"pid-{account}",
        account_name=account, task_name=task,
        started_at=iso_now(), completed_at=iso_now(),
        status="failed", confidence="failed",
        model=model, model_used=model,
        input_tokens=0, output_tokens=0,
        cached_input_tokens=0, search_count=0,
        duration_seconds=1.0,
        error=error,
    ))


def _record_degraded(
    rl: RunLog,
    *,
    account: str,
    task: str,
    output_json: str,
    model: str = "claude-sonnet-4-6",
) -> None:
    """A status='success' row where the model proceeded after a tool error.
    Used to verify that quota-degraded rows are counted, not just failed ones."""
    rl.record(RunRecord(
        account_page_id=f"pid-{account}",
        account_name=account, task_name=task,
        started_at=iso_now(), completed_at=iso_now(),
        status="success", confidence="low",
        model=model, model_used=model,
        input_tokens=1_000, output_tokens=100,
        cached_input_tokens=0, search_count=0,
        duration_seconds=1.0,
        output_json=output_json,
    ))


def test_cost_summary_counts_failed_rows(tmp_path) -> None:
    rl = RunLog(tmp_path / "runs.db")
    _record(rl, account="A", task="ok",
            model="claude-sonnet-4-6", input_tokens=1_000, output_tokens=100)
    _record_failed(rl, account="A", task="bad", error="some-other-failure")
    _record_failed(rl, account="B", task="bad", error="another")
    cs = rl.cost_summary()
    assert cs["failed_rows"] == 2
    assert cs["tavily_quota_affected"] == 0
    # Successful row's cost still counts.
    assert cs["total_usd"] > 0


def test_cost_summary_recognizes_tavily_432_in_errors(tmp_path) -> None:
    """Tavily quota exhaustion (HTTP 432) is a known failure mode — the
    cost summary attributes it explicitly so the CLI can warn the user."""
    rl = RunLog(tmp_path / "runs.db")
    _record_failed(
        rl, account="A", task="research_pass",
        error="HTTPError: 432 Client Error:  for url: https://api.tavily.com/search",
    )
    _record_failed(
        rl, account="B", task="module_11_hiring_signal",
        error="HTTPError: 432 Client Error:  for url: https://api.tavily.com/search",
    )
    _record_failed(rl, account="C", task="bad", error="unrelated runtime error")
    cs = rl.cost_summary()
    assert cs["failed_rows"] == 3
    assert cs["tavily_quota_affected"] == 2


def test_cost_summary_counts_degraded_success_rows_too(tmp_path) -> None:
    """The most common Tavily-432 outcome is NOT status=failed — the model
    sees the 432 short-circuit in a tool_result and proceeds with low
    confidence. Those rows must count too, or the quota warning under-fires."""
    rl = RunLog(tmp_path / "runs.db")
    _record_degraded(
        rl, account="A", task="module_07",
        output_json='{"confidence":"low","sources":[],"raw":"Tool returned: ERROR: Tavily monthly quota exhausted (HTTP 432)"}',
    )
    _record_degraded(
        rl, account="B", task="module_13",
        output_json='{"confidence":"low","note":"Could not search; HTTP 432 from Tavily"}',
    )
    _record(rl, account="C", task="ok",
            model="claude-sonnet-4-6", input_tokens=1_000, output_tokens=100)
    cs = rl.cost_summary()
    assert cs["failed_rows"] == 0  # degraded rows are NOT failures
    assert cs["tavily_quota_affected"] == 2


def test_cost_summary_no_failures_returns_zero_counts(tmp_path) -> None:
    rl = RunLog(tmp_path / "runs.db")
    _record(rl, account="A", task="t1",
            model="claude-sonnet-4-6", input_tokens=1_000, output_tokens=100)
    cs = rl.cost_summary()
    assert cs["failed_rows"] == 0
    assert cs["tavily_quota_affected"] == 0


def test_cost_summary_does_not_misclassify_non_tavily_432(tmp_path) -> None:
    """A 432 from a different service shouldn't be attributed to Tavily quota."""
    rl = RunLog(tmp_path / "runs.db")
    _record_failed(
        rl, account="A", task="some_task",
        error="HTTPError: 432 from some other service",
    )
    cs = rl.cost_summary()
    assert cs["failed_rows"] == 1
    assert cs["tavily_quota_affected"] == 0
