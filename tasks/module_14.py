"""Module 14 — Hiring/downsizing signal (v1.4.0 adds ATS direct read).

Tools:
  - hiring_signals (jobspy) — secondary boards (Indeed/LinkedIn/Glassdoor/ZR)
  - ats_jobs (Greenhouse public API) — primary ATS, 3-5x richer for B2B SaaS
  - web_search (Tavily) — layoff news

Outputs:
  - Buying Signals multi-select tag (hiring | downsizing)
  - Headcount subsection paragraph + important-role bullets
  - signal_section under News for the Buying Signal tag
"""

from __future__ import annotations

from typing import Any

import crm
import prompts.module_14_hiring_signal as prompt
from tasks.base import DetectedEvent, Task, _today_header
from tools.ats_fetcher import ATSFetcherTool
from tools.base import Tool
from tools.hiring_signals import HiringSignalsTool
from tools import build_search_tool


# Short labels for the ATS-fetcher tier IDs — readable on the page body.
_TIER_LABEL = {
    "tier_1_marketing_ai": "marketing + AI",
    "tier_2_senior_leadership": "senior leadership",
    "tier_3_senior_creative_ic": "senior creative IC",
}


class Module14HiringSignal(Task):
    name = "module_14_hiring_signal"
    section = "Overview"
    subsection = "Headcount"
    prompt_module = prompt

    def build_tools(self) -> list[Tool]:
        return [HiringSignalsTool(), ATSFetcherTool(), build_search_tool()]

    def build_user_message(self, account_name: str, context: dict[str, Any]) -> str:
        """Inject gate output so the model skips redundant searches AND uses
        region-aware hiring queries (Fix Appendix #12). Also surface the
        greenhouse_slug captured by research_pass so the model passes it
        authoritatively to ats_jobs instead of relying on name-derived
        heuristics that miss for companies whose slug ≠ brand name."""
        gate = context.get("module_01_gate") or {}
        news = context.get("module_06_structural_news") or {}
        rp = context.get("research_pass") or {}
        prior: list[str] = []

        emp = gate.get("employee_count_estimate")
        if isinstance(emp, int):
            prior.append(f"company size confirmed at ~{emp:,} employees")

        regions = gate.get("regions_present") or []
        if not regions:
            # Fall back to a sane default when the gate didn't surface countries.
            if gate.get("operates_in_na"):
                regions = ["USA"]
            elif gate.get("operates_in_eu"):
                regions = ["UK", "Germany"]
        regions_str = ", ".join(regions) if regions else "USA"

        if news.get("structure_note") == "mass layoffs":
            prior.append(f"recent layoff event already detected: {news.get('event_summary','')}")

        prior_block = ("Prior research established: " + "; ".join(prior) + ". ") if prior else ""

        # Authoritative Greenhouse slug captured by research_pass. When
        # present, the model MUST pass it through verbatim (don't guess /
        # don't second-guess). When missing, the tool falls back to name-
        # derived heuristics — which is what was happening today.
        gh_slug = rp.get("greenhouse_slug")
        if gh_slug:
            ats_instruction = (
                f"Then call ats_jobs(provider='greenhouse', ats_slug={gh_slug!r}) "
                "— this slug was confirmed by upstream research, pass it "
                "verbatim. "
            )
        else:
            ats_instruction = (
                "Then call ats_jobs (provider='greenhouse') — look at the "
                "company's careers page to find the Greenhouse slug if you "
                "can; pass it as `ats_slug=<slug>`. If you can't find a slug, "
                "call ats_jobs without one and the tool will try heuristics "
                "from the company name. "
            )

        return (
            f"{_today_header()}\n\n"
            f"Research the company: {account_name}.\n"
            f"{prior_block}"
            f"When you call hiring_signals, pass countries={regions} to scrape "
            f"each market the company operates in ({regions_str}). "
            f"{ats_instruction}"
            f"ATS data typically returns 3-5× more roles than hiring_signals "
            f"for B2B SaaS and includes important-role open/close diffing. "
            f"Only call web_search for layoff news if the layoff context isn't "
            f"already clear from the prior structural-news context above "
            f"(use days=90, fall back to days=180 only if no 90-day result)."
        )

    def to_fields(self, output: dict[str, Any]) -> dict[str, Any]:
        signal = output.get("headcount_signal")
        if signal in ("hiring", "downsizing"):
            return {
                crm.PROP_BUYING_SIGNALS: {
                    "multi_select": [{"name": signal}],
                }
            }
        return {}

    def to_blocks(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        """Headcount subsection: summary paragraph, then highlight bullets for
        any important roles that are open or recently closed. The summary
        carries `[N]` citation markers; the orchestrator rewrites them into
        clickable links per the per-section citation pass."""
        summary = output.get("headcount_summary")
        if not summary:
            return []
        blocks: list[dict[str, Any]] = [crm.paragraph(summary)]

        important_open = output.get("important_roles_open") or []
        important_closed = output.get("important_roles_recently_closed") or []

        if important_open:
            in_scope_count = sum(1 for r in important_open if r.get("in_scope") is True)
            oos_count = sum(1 for r in important_open if r.get("in_scope") is False)
            unk_count = sum(1 for r in important_open if r.get("in_scope") is None)
            mix_label = (
                f"{in_scope_count} in UK/EU/NA"
                + (f", {oos_count} elsewhere" if oos_count else "")
                + (f", {unk_count} unknown location" if unk_count else "")
            )
            blocks.append(crm.paragraph(
                f"Important roles currently open ({len(important_open)} — {mix_label}):"
            ))
            for r in important_open[:10]:
                title = r.get("title", "?")
                loc = r.get("location") or ""
                tier = r.get("tier", "")
                tier_label = _TIER_LABEL.get(tier, tier or "important")
                in_scope = r.get("in_scope")
                scope_marker = (
                    "" if in_scope is True
                    else " · out-of-scope" if in_scope is False
                    else " · location TBD"
                )
                loc_suffix = f" — {loc}" if loc else ""
                blocks.append(crm.bullet(
                    f"{title} [{tier_label}{scope_marker}]{loc_suffix}"
                ))

        if important_closed:
            blocks.append(crm.paragraph(
                f"Important roles closed since previous research run "
                f"({len(important_closed)}) — recent closure may indicate the "
                f"team is now in place and ramping creative/marketing investment:"
            ))
            for r in important_closed[:10]:
                title = r.get("title", "?")
                tier = r.get("tier", "")
                tier_label = _TIER_LABEL.get(tier, tier or "important")
                blocks.append(crm.bullet(f"{title} [{tier_label}]"))

        return blocks

    def detect_events(
        self,
        prev_output: dict[str, Any] | None,
        curr_output: dict[str, Any] | None,
    ) -> list[DetectedEvent]:
        """Two distinct alert paths:

        1. `important_roles_recently_closed` — already diffed by ATSSnapshotStore
           between snapshots. A Tier-1 or Tier-2 closure means the company just
           hired for a senior creative/marketing role; the team is now in place
           and ramping. Read straight from curr_output (no prev diff needed —
           the tool already did the work).

        2. New Tier-1 role appearing in `important_roles_open` that wasn't in
           the prior snapshot's open list. The snapshot store surfaces CLOSURES
           but not class-additions; diff externally.
        """
        if curr_output is None:
            return []
        events: list[DetectedEvent] = []

        # Path 1: closures since previous snapshot. Tier 1+2 only.
        closed = curr_output.get("important_roles_recently_closed") or []
        for r in closed:
            tier = r.get("tier") or ""
            if tier not in ("tier_1_marketing_ai", "tier_2_senior_leadership"):
                continue
            title = r.get("title") or ""
            if not title:
                continue
            sig = f"module_14:closed:{tier}:{title.lower()}"
            events.append(DetectedEvent(
                account_page_id="",
                module=self.name,
                signal_type="senior-hire",
                summary=(
                    f"Senior role just filled ({_TIER_LABEL.get(tier, tier)}): "
                    f"{title} — team is in place, ramping."
                ),
                source_url=None,
                signature=sig,
            ))

        # Path 2: new Tier-1 opens not present in prev. First-run guard.
        if prev_output is not None:
            prev_open = prev_output.get("important_roles_open") or []
            prev_tier1_titles = {
                (r.get("title") or "").strip().lower()
                for r in prev_open
                if r.get("tier") == "tier_1_marketing_ai"
            }
            for r in curr_output.get("important_roles_open") or []:
                if r.get("tier") != "tier_1_marketing_ai":
                    continue
                title = (r.get("title") or "").strip()
                if not title or title.lower() in prev_tier1_titles:
                    continue
                # In-scope only (UK+EU+NA) — out-of-scope Tier-1 opens are too
                # noisy for an alert (per Module 14's location-aware filter).
                if r.get("in_scope") is not True:
                    continue
                sig = f"module_14:open_tier1:{title.lower()}"
                events.append(DetectedEvent(
                    account_page_id="",
                    module=self.name,
                    signal_type="senior-hire",
                    summary=(
                        f"New marketing+AI role open in UK/EU/NA: {title}"
                        + (f" — {r.get('location')}" if r.get("location") else "")
                    ),
                    source_url=r.get("url"),
                    signature=sig,
                ))

        return events

    def to_signal_sections(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        signal = output.get("headcount_signal")
        if signal not in ("hiring", "downsizing"):
            return []
        if signal == "hiring":
            global_roles = output.get("creative_marketing_roles_count")
            in_scope_roles = output.get("creative_marketing_roles_in_scope_count", global_roles)
            total = output.get("active_open_roles_total")
            titles = output.get("creative_marketing_role_titles") or []
            titles_blurb = (
                f" Notable open roles: {', '.join(titles[:5])}." if titles else ""
            )
            # Build the count phrasing — when global and in-scope differ
            # materially, surface both so the BDR knows the geographic
            # concentration (UK+EU+NA is the addressable footprint).
            if isinstance(global_roles, int) and isinstance(in_scope_roles, int) \
                    and global_roles > in_scope_roles:
                count_phrase = (
                    f"{in_scope_roles} of {global_roles} creative/marketing role(s) "
                    f"open in UK/EU/NA out of {total} total globally"
                )
            else:
                count_phrase = (
                    f"{in_scope_roles or global_roles} creative/marketing role(s) "
                    f"open in UK/EU/NA out of {total} total"
                )
            logic = (
                f"Active hiring detected in UK/EU/NA — {count_phrase}, "
                f"above the threshold (>=3 in-scope) that flags a hiring "
                f"posture.{titles_blurb}"
            )
        else:  # downsizing
            layoff_summary = output.get("layoff_summary") or output.get("headcount_summary") or ""
            logic = (
                f"Downsizing detected within the last 6 months. {layoff_summary}"
            )
        sources = list(output.get("sources") or [])
        return [{"signal": signal, "logic": logic, "sources": sources}]


