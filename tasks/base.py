"""Task framework — one Task per research question.

Each Task subclass declares:
  - name:           short identifier (used in CLI + run log)
  - section:        which page-body section its blocks land under
                    (e.g. "Overview", "News", "Creative Posture", ...)
  - subsection:     optional sub-heading inside the section (e.g. "Headcount")
  - prompt_module:  the prompts/ module providing VERSION + SYSTEM_PROMPT
  - tools:          list of Tool factories used during the ReAct loop

Subclasses override:
  - to_fields(output): map model output -> Notion property update payload
  - to_blocks(output): map model output -> Notion children blocks for the page body
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from types import ModuleType
from typing import Any

import config
from providers.base import LLMProvider
from tools.base import Tool


def _today_header() -> str:
    """Absolute-date header prepended to every task's user message.

    The model otherwise has no idea what "today" is and treats "last 6 months"
    as relative to its training cutoff — which is exactly how 2024-vintage
    "recent" news ended up in research outputs. Anchoring with an explicit
    "Today is YYYY-MM-DD" line in the user turn fixes that without touching
    each prompt's system text.

    v2 (2026-05-12): also spell out the three news-recency cutoff dates so
    the model doesn't have to do date math and can't misread "12 months
    ago" as "early 2024" when today is mid-2026. Recency tiers:
      - 12 months: big structural events (M&A, IPO, bankruptcy, layoffs,
        major leadership change) — items dated before this MUST be dropped.
      - 6 months: relevant non-structural news (smaller events, product
        launches, regional moves).
      - 90 days: time-sensitive buying signals (funding round, rebrand,
        agency switch, AI initiative, hiring posture).
    """
    from datetime import timedelta
    today = date.today()
    twelve_mo = (today - timedelta(days=365)).isoformat()
    six_mo = (today - timedelta(days=183)).isoformat()
    ninety_d = (today - timedelta(days=90)).isoformat()
    return (
        f"Today is {today.isoformat()}.\n"
        f"News-recency cutoffs (drop ANY item older than the applicable cutoff):\n"
        f"  - 12-month cutoff (big events): {twelve_mo}\n"
        f"  - 6-month cutoff (relevant news): {six_mo}\n"
        f"  - 90-day cutoff (buying signals): {ninety_d}\n"
        f"DO NOT trust your training-data freshness — the cutoffs above are "
        f"the only valid anchor. Items dated 2024 or earlier are out of scope "
        f"unless they're documented STILL-ACTIVE structural state (e.g. a 2023 "
        f"merger that's still the current corporate structure)."
    )


@dataclass
class DetectedEvent:
    """Phase B (2026-05-13): one alert-worthy transition detected in a per-task
    diff. The orchestrator collects these per account and forwards them to the
    writeback's alerting path (`Needs Attention` multi_select + one Notion
    comment summarising all events for the run).

    Fields:
    - account_page_id: which Notion page the alert is for
    - module: the task that detected the transition (e.g. "module_05_structural_news")
    - signal_type: short tag matching crm.NEEDS_ATTENTION_OPTIONS (e.g. "bankruptcy")
    - summary: 1-2 sentence human-readable description of what changed
    - source_url: optional canonical source for the alert (often a citation URL)
    - signature: stable string used by event_alerts dedup. Should hash to the
      same value for "the same underlying event" across consecutive runs so the
      14-day cooldown actually suppresses repeats.
    """
    account_page_id: str
    module: str
    signal_type: str
    summary: str
    signature: str
    source_url: str | None = None


@dataclass
class TaskResult:
    task_name: str
    output: dict[str, Any] | None       # parsed JSON from the agent
    confidence: str                     # "high" | "medium" | "low" | "failed"
    fields: dict[str, Any]              # Notion property updates this task produced
    page_blocks: list[dict[str, Any]]   # Notion blocks to append to the page body
    section: str                        # which section these blocks go under
    subsection: str | None              # optional sub-section heading
    sources: list[str]
    search_count: int
    input_tokens: int
    output_tokens: int
    duration_seconds: float
    prompt_version: str
    provider_name: str
    cached_input_tokens: int = 0        # tokens served from prompt cache
    model_used: str | None = None        # provider-specific model ("claude-haiku-4-5" etc.)
    # Fix Appendix #6: URLs the model actually saw via tools, used to verify
    # `output.sources ⊆ tool_results_seen` in evals. Empty list when no tools ran.
    tool_results_seen: list[str] = field(default_factory=list)
    # Per-detected-signal subsections rendered under News on the page body.
    # Each item: {"signal": <PROP_BUYING_SIGNALS option>, "logic": str, "sources": list[str]}.
    # Only modules that contribute to PROP_BUYING_SIGNALS populate this.
    signal_sections: list[dict[str, Any]] = field(default_factory=list)
    # Citations the model emitted alongside narrative text. Each item:
    # {"n": int, "title": str, "url": str}. The model places module-local `[N]`
    # markers inline in page-block text; the orchestrator re-numbers citations
    # globally per page-body section and rewrites the markers to be clickable
    # Notion link spans (see orchestrator._rewrite_block_citation_markers).
    citations: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class Task:
    """A single research question. Subclasses set the class-level attributes below."""

    name: str = ""
    section: str = "Overview"
    subsection: str | None = None
    prompt_module: ModuleType | None = None  # set in subclass: `import prompts.module_NN as ...`
    model_tier: str = "smart"   # "smart" (default Sonnet/gpt-4.1) | "fast" (Haiku/mini)
    cache_system_prompt: bool = True   # toggle caching per-task if needed

    # If True, this task does NOT use tools or run an agent loop. It receives the
    # ResearchPass output via context and produces its structured JSON in a single
    # LLM call. Use for tasks whose source material is fully derivable from the
    # shared research context. Saves ~50-70% per task vs the full agent loop.
    synthesis_only: bool = False

    # If True, the orchestrator skips this task on every cadence run after its
    # first non-error success for a given account, replaying the cached
    # output_json from runs.db. Use for one-time gates (e.g. size, persona
    # headcount) — facts that don't drift account-by-account once established.
    run_once: bool = False

    def build_tools(self) -> list[Tool]:
        """Return fresh Tool instances for this run (per-task call counters)."""
        return []

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        return {}

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def post_parse(self, raw_text: str, output: dict[str, Any]) -> dict[str, Any]:
        """Optional enrichment of the parsed JSON dict from the raw model output.

        Default no-op. ResearchPass overrides to pull a delimited prose block
        (raw_research) out of `raw_text` — keeping bulky markdown OUT of the
        JSON avoids string-escape bugs that broke Mendix and Roblox runs on
        2026-05-12 (unescaped `"` inside a multi-line `raw_research` string
        flipped the bracket-balanced parser's `in_string` state and made the
        whole JSON unrecoverable, even though output_tokens were under cap).
        """
        return output

    def to_signal_sections(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        """Per-detected-signal subsections appended under News.

        Only tasks that write tags to PROP_BUYING_SIGNALS override this. Each entry:
          {"signal": <signal name>, "logic": <1-3 sentences>, "sources": [url, ...]}.
        Empty list when no signals detected this run.
        """
        return []

    def detect_events(
        self,
        prev_output: dict[str, Any] | None,
        curr_output: dict[str, Any] | None,
    ) -> list["DetectedEvent"]:
        """Phase B (2026-05-13): per-task diff hook. Compare this run's output
        with the most recent prior successful run for the same (account, task)
        and return alert-worthy transitions.

        Default: empty list (no events for this module).

        Contract:
        - prev_output is None on first detection or after a prompt_version
          mismatch — return [] (don't alert on initial state, alerts trigger
          on TRANSITIONS only).
        - curr_output is None when the current run failed — return [].
        - Each returned DetectedEvent must have a stable `signature` so the
          orchestrator's alert dedup table (`event_alerts`) can suppress
          repeats within the cooldown window.
        """
        return []

    def build_user_message(self, account_name: str, context: dict[str, Any]) -> str:
        """Default: just ask the model to research the company. Subclasses override
        to inject prior-task context, which lets the agent skip redundant searches.

        Synthesis-only tasks use the helper below to fold ResearchPass output in.

        Every task's user message is prefixed with `Today is YYYY-MM-DD.` so the
        model can resolve relative time windows ("last 3 months") to absolute
        dates instead of inheriting them from its training cutoff.
        """
        if self.synthesis_only:
            return self.synthesis_user_message(account_name, context)
        return f"{_today_header()}\n\nResearch the company: {account_name}"

    def synthesis_user_message(self, account_name: str, context: dict[str, Any]) -> str:
        """Build a synthesis-only user message that embeds the shared research context.
        Override in subclasses if a task needs to also reference earlier synthesis outputs."""
        research = (context.get("research_pass") or {}).get("raw_research", "")
        sources = (context.get("research_pass") or {}).get("sources", [])
        sources_block = "\n".join(f"- {u}" for u in sources) if sources else "(no sources captured)"
        if not research:
            # Fallback if research_pass didn't run — model has to do its own searches
            # via tools (but synthesis_only=True means it has no tools). Surface this
            # clearly so the model returns confidence=low rather than hallucinating.
            return (
                f"Company: {account_name}.\n\n"
                "No upfront research context is available. Return your best-effort "
                "answer based on general knowledge, but set confidence='low' and "
                "leave fields null where you cannot verify."
            )
        return (
            f"{_today_header()}\n\n"
            f"Company: {account_name}.\n\n"
            f"## Research context (from upfront research pass)\n\n"
            f"{research}\n\n"
            f"## Sources\n{sources_block}\n\n"
            "Using ONLY the research context above, fill out your task's JSON schema. "
            "Do not invent facts that aren't present in the research context — set "
            "fields to null and lower confidence if the research doesn't cover them. "
            "The 'sources' field in your output must be a subset of the URLs above."
        )

    # ---- run loop ----

    def run(
        self, account_name: str, provider: LLMProvider,
        context: dict[str, Any] | None = None,
    ) -> TaskResult:
        if self.prompt_module is None:
            raise RuntimeError(f"Task {self.name!r} has no prompt_module set.")

        system_prompt = self.prompt_module.SYSTEM_PROMPT
        prompt_version = getattr(self.prompt_module, "VERSION", "unknown")
        ctx = context or {}

        # Build the user message. If `context` contains relevant prior task outputs
        # this task can use, the subclass overrides build_user_message() to inject
        # them (saves search iterations).
        user_message = self.build_user_message(account_name, ctx)

        # Synthesis-only tasks skip tools entirely — single LLM call, no agent loop.
        # The shared research context is folded into user_message via the subclass's
        # build_user_message() override.
        if self.synthesis_only:
            tools: list[Tool] = []
            max_iter = 1
            # Short-circuit: a synthesis-only task without research_pass context
            # has nothing real to synthesize from — it would be asked to fabricate
            # from training data with no source verification. "Wrong data is
            # worse than missing data" — return failed so writeback gates this.
            research = (ctx.get("research_pass") or {}).get("raw_research", "")
            if not research:
                return TaskResult(
                    task_name=self.name, output=None, confidence="failed",
                    fields={}, page_blocks=[], section=self.section, subsection=self.subsection,
                    sources=[], search_count=0,
                    input_tokens=0, output_tokens=0,
                    duration_seconds=0.0,
                    prompt_version=prompt_version, provider_name=provider.name,
                    cached_input_tokens=0,
                    model_used=None,
                    tool_results_seen=None,
                    error="synthesis-only task short-circuited: research_pass produced no raw_research",
                )
        else:
            tools = self.build_tools()
            max_iter = config.MAX_AGENT_ITERATIONS

        start = time.time()
        result = provider.run_loop(
            system_prompt=system_prompt,
            user_message=user_message,
            tools=tools,
            max_iterations=max_iter,
            model_tier=self.model_tier,
            cache_system_prompt=self.cache_system_prompt,
        )
        duration = time.time() - start

        observed_urls = _collect_observed_urls(tools)

        if result.error or result.stop_reason != "end_turn":
            return TaskResult(
                task_name=self.name, output=None, confidence="failed",
                fields={}, page_blocks=[], section=self.section, subsection=self.subsection,
                sources=[],
                search_count=sum(t.call_count for t in tools),
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                duration_seconds=duration,
                prompt_version=prompt_version, provider_name=provider.name,
                cached_input_tokens=result.cached_input_tokens,
                model_used=result.model_used,
                tool_results_seen=observed_urls,
                error=result.error or f"stop_reason: {result.stop_reason}",
            )

        output = _extract_json(result.text)
        if output is None:
            return TaskResult(
                task_name=self.name, output=None, confidence="failed",
                fields={}, page_blocks=[], section=self.section, subsection=self.subsection,
                sources=[],
                search_count=sum(t.call_count for t in tools),
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                duration_seconds=duration,
                prompt_version=prompt_version, provider_name=provider.name,
                cached_input_tokens=result.cached_input_tokens,
                model_used=result.model_used,
                tool_results_seen=observed_urls,
                error=f"end_turn without JSON block:\n{result.text[:800]}",
            )

        output = self.post_parse(result.text, output)

        confidence = output.get("confidence", "low")
        # "failed" is the model's self-report that it couldn't do the work;
        # the orchestrator flows it through to overall_status="failed" and
        # gates the Notion write. Don't silently downgrade to "low" — that
        # would let bad data slip into Notion with status=needs_review.
        if confidence not in {"high", "medium", "low", "failed"}:
            confidence = "low"

        # Inject the canonical Notion account name as ephemeral context for the
        # to_* hooks. Popped before result construction so it never lands in
        # the run log's output_json or in downstream task context envelopes.
        # Used by module_04 (corporate structure) to write the Notion
        # `Parent` property as the account's own name when
        # structure_type == "parent".
        output["_account_name"] = account_name
        fields = self.to_fields(output)
        page_blocks = self.to_blocks(output)
        signal_sections = self.to_signal_sections(output)
        output.pop("_account_name", None)

        return TaskResult(
            task_name=self.name, output=output, confidence=confidence,
            fields=fields, page_blocks=page_blocks,
            section=self.section, subsection=self.subsection,
            sources=output.get("sources", []) or [],
            search_count=sum(t.call_count for t in tools),
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            duration_seconds=duration,
            prompt_version=prompt_version, provider_name=provider.name,
            cached_input_tokens=result.cached_input_tokens,
            model_used=result.model_used,
            tool_results_seen=observed_urls,
            signal_sections=signal_sections,
            citations=_extract_citations(output),
        )


def _extract_citations(output: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull a well-formed citations array out of the model output.

    Each citation must be a dict with integer `n`, non-empty `url`, and string
    `title`. Anything malformed is silently dropped (the model occasionally
    half-writes a citation; we'd rather lose one than fail the whole task).
    """
    raw = output.get("citations") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        n = c.get("n")
        url = c.get("url")
        if not isinstance(n, int) or not isinstance(url, str) or not url:
            continue
        out.append({
            "n": n,
            "title": str(c.get("title") or ""),
            "url": url,
        })
    return out


def _collect_observed_urls(tools: list[Tool]) -> list[str]:
    """Aggregate `observed_urls` across every tool that exposes the property,
    de-duped while preserving first-seen order. Tools without the attribute
    contribute nothing — keeps the contract opt-in for future tools."""
    seen: set[str] = set()
    out: list[str] = []
    for t in tools:
        urls = getattr(t, "observed_urls", None)
        if not urls:
            continue
        for u in urls:
            if u and u not in seen:
                seen.add(u)
                out.append(u)
    return out


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull a JSON object out of model output.

    Strategy (Fix Appendix #5):
      1. Prefer a ```json fenced block — providers that follow the prompt put it there.
      2. Fall back to bracket-balanced candidates. The previous regex `\\{.*?\\}` was
         non-greedy and could capture a wrong slice on nested JSON (closes at the first
         `}`). Bracket balancing finds every top-level `{...}` correctly, even with
         nested objects/arrays and quoted braces inside strings.

    Includes a tolerant repair pass for common provider quirks observed in evals:
      - gpt-4.1 sometimes writes numeric ranges (e.g. `1000-5000`) as values where
        an integer is expected. Repair: replace `<number>-<number>` value with null.
      - Trailing commas before `]` or `}`.
    """
    candidates = _json_candidates(text)
    for candidate in candidates:
        for parser in (_strict_json, _repaired_json):
            result = parser(candidate)
            if result is not None:
                return result
    return None


def _json_candidates(text: str) -> list[str]:
    """Return JSON candidate strings ordered most-trusted first.

    A ```json fence is the most explicit signal the model has followed
    instructions; bracket-balanced fallbacks let us recover when the model
    omits the fence.

    Three tiers (2026-05-12 hardening):
      1. Closed fenced block: ```json...``` — strongest signal.
      2. Open fenced block: ```json with no closing fence — happens when
         the model wraps the JSON in a fence but forgets to close it. We
         scan from the first `{` after ```json and walk to the matching `}`.
      3. Bracket-balanced fallback over the whole text — handles models
         that emit the JSON without any fence at all.
    """
    fenced = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        return [fenced.group(1)]

    # Tier 2: open fenced block. Find the ```json marker, then scan from the
    # first `{` after it to its matching closing brace. Handles "wrote a
    # fence opener, forgot the closer" cases (intermittent end_turn quirk
    # observed in production runs).
    open_fence = re.search(r"```json\s*", text)
    if open_fence:
        rest = text[open_fence.end():]
        first_brace = rest.find("{")
        if first_brace >= 0:
            candidate = _walk_balanced_object(rest[first_brace:])
            if candidate:
                return [candidate]

    candidates: list[str] = []
    starts = [i for i, ch in enumerate(text) if ch == "{"]
    for start in starts:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break
    return candidates


def _walk_balanced_object(s: str) -> str | None:
    """Starting at index 0 (expected to be `{`), return the substring up to
    and including the matching closing `}`. Returns None if no balanced
    object is found (truncated input). Quoted strings + escape sequences
    are respected so brace characters inside JSON string values don't
    throw off the depth count."""
    if not s or s[0] != "{":
        return None
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(s):
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[:i + 1]
    return None


def _strict_json(s: str) -> dict[str, Any] | None:
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _repaired_json(s: str) -> dict[str, Any] | None:
    """Attempt to repair common provider JSON quirks. Returns the parsed dict
    or None if repair didn't help."""
    repaired = s
    # Replace numeric ranges in value position with null. Matches `: 1000-5000`
    # but NOT `: "1000-5000"` (the latter is a valid string value).
    repaired = re.sub(r':\s*-?\d+(?:\.\d+)?\s*-\s*-?\d+(?:\.\d+)?(?=\s*[,}])',
                       ': null', repaired)
    # Strip trailing commas before } or ]
    repaired = re.sub(r',\s*([}\]])', r'\1', repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None
