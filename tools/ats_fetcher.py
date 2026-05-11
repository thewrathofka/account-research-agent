"""ATSFetcherTool — hits applicant-tracking-system (ATS) public APIs to get the
canonical list of a company's open roles. Greenhouse is supported today; Lever
/ Ashby / Workday left as stubs.

Why this exists:
  jobspy aggregates secondary boards (Indeed, LinkedIn, Glassdoor, ZipRecruiter)
  which under-report against the company's own ATS. AlphaSense, for instance,
  had 198 open roles on Greenhouse but only 38 on jobspy (USA only, 0 marketing/
  creative). A direct ATS read closes that gap — Greenhouse's public API is
  unauthenticated and rate-limit-friendly.

What the tool returns:
  - Total open roles + per-location breakdown
  - The full list of titles for the model to classify
  - `important_roles`: roles that match a curated pattern bank for marketing /
    creative / brand / AI + content-strategy work (the three-tier classifier
    below — see is_important_title and IMPORTANT_TITLE_PATTERNS).
  - `recently_closed_important_roles`: roles that were in the company's
    PREVIOUS snapshot for this ATS provider but are no longer present. Since
    snapshots are taken per-run, "recently" means "since the prior research
    pass for this account." Closures of important roles are buying signals too:
    a company that just hired a Senior Motion Designer two weeks ago is now
    spinning up creative production capacity.

Snapshot persistence:
  Each fetch stores the (company, provider, role-list, timestamp) into the
  SQLite run_log so subsequent runs can diff against it. See run_log.ATSSnapshotStore.

Slug discovery:
  Greenhouse boards are keyed by a slug (e.g. alphasense's URL is
  https://job-boards.greenhouse.io/alphasense/...). The model should pass the
  slug after observing the company's careers page. As a fallback we try a few
  heuristic transforms of the company name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from rate_limit import RateLimiter
from run_log import ATSSnapshotStore


# Greenhouse rate-limits at roughly 1 req/sec per IP — keep generous headroom.
GREENHOUSE_LIMITER = RateLimiter(rate_per_second=2)
GREENHOUSE_API_BASE = "https://boards-api.greenhouse.io/v1/boards"


# ============================================================================
# Important-title pattern bank — three tiers (see SKILL: prompts/_citations or
# the in-tree feedback_arr_narrative_style memory for the broader contract).
# ============================================================================

_RE = re.IGNORECASE | re.VERBOSE

# Tier 1: AI + marketing / brand / creative / content / design (Kali's
# explicit ask). The order is flexible — either "Marketing AI ..." or "AI
# Marketing ..." should match. Also covers automation / transformation /
# ML wording that often signals the same investment shape.
_TIER1_RE = re.compile(r"""
    \b
    (?:
        (?:marketing|brand|creative|content|design|copy)
        [\w\s,/&\-]{0,60}
        \b(?:ai|ml|automation|transformation|gpt|llm|genai|generative)\b
      |
        \b(?:ai|ml|automation|transformation|gpt|llm|genai|generative)\b
        [\w\s,/&\-]{0,60}
        (?:marketing|brand|creative|content|design|copy)
    )
    \b
""", _RE)

# Tier 2: senior leadership in marketing / brand / creative / content
# functions. New senior leader = ~6 month creative-ops rebuild ahead.
# Two alternatives:
#   (a) `Head / VP / Director / Chief / SVP` + (marketing | brand | …) somewhere after
#   (b) Level-implicit senior titles like Creative Director, Art Director,
#       Brand Director, CMO, CCO — these are inherently senior even without
#       an "of X" suffix.
_TIER2_RE = re.compile(r"""
    (?:
        \b
        (?:head|vp|vice\s+president|director|chief|svp)
        \b
        [\w\s,/&\-]{0,40}
        \b
        (?:
            marketing | brand | creative | content | design | growth |
            demand\s*gen(?:eration)? | performance\s+marketing | rev\s*ops |
            revenue\s+operations | marketing\s+operations
        )
        \b
      |
        \b(?:cmo|cco|cmo|chief\s+marketing\s+officer|chief\s+creative\s+officer|chief\s+brand\s+officer)\b
      |
        \b(?:creative|art|brand|content|design)\s+director\b
    )
""", _RE)

# Tier 3: senior individual contributors in creative production. These
# directly hint at building/scaling creative capacity.
_TIER3_RE = re.compile(r"""
    \b
    (?:senior|principal|staff|lead|sr\.?)
    \b
    [\w\s,/&\-]{0,30}
    \b
    (?:
        designer | motion\s+designer | brand\s+designer | product\s+designer |
        creative\s+director | art\s+director | copywriter |
        content\s+strategist | content\s+designer | ux\s+designer
    )
    \b
""", _RE)


# Tier-name → (regex, human-readable description). Order matters: Tier 1
# is checked first so "Marketing AI Strategy Lead" is classified as tier 1
# even though it also matches tier 2 (Director-level).
IMPORTANT_TITLE_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("tier_1_marketing_ai", _TIER1_RE,
     "AI/ML/automation in marketing/brand/creative/content/design"),
    ("tier_2_senior_leadership", _TIER2_RE,
     "Senior leadership (Head/VP/Director/Chief) in marketing/brand/creative function"),
    ("tier_3_senior_creative_ic", _TIER3_RE,
     "Senior/Principal/Staff/Lead creative IC role (designer, copywriter, content)"),
]


def classify_important_title(title: str) -> tuple[str, str] | None:
    """Return (tier_id, tier_description) for the first matching tier, or None.

    First-match-wins so a Tier-1 hit doesn't double-count as Tier 2. The
    three-tier ordering reflects buying-signal strength (Tier 1 highest).
    """
    if not title:
        return None
    for tier_id, pattern, description in IMPORTANT_TITLE_PATTERNS:
        if pattern.search(title):
            return (tier_id, description)
    return None


# ============================================================================
# Tool schema (provider-neutral)
# ============================================================================

ATS_FETCHER_SCHEMA: dict[str, Any] = {
    "name": "ats_jobs",
    "description": (
        "Look up a company's open roles directly from its applicant-tracking-"
        "system (ATS) public API. Currently supports Greenhouse boards "
        "(`provider=\"greenhouse\"`). Use this AFTER `hiring_signals` to "
        "supplement secondary-board scraping — ATS direct reads typically "
        "return 3-5x more roles than aggregator-board scrapers, especially "
        "for B2B SaaS companies that strategically don't syndicate every "
        "role.\n\n"
        "How to find the slug: look at the company's careers page. If the "
        "URL is `job-boards.greenhouse.io/<X>/...` or the page embeds a "
        "Greenhouse iframe, X is the slug. Often it's the company name "
        "lowercased with spaces removed (e.g. AlphaSense → 'alphasense'). "
        "If you're not sure, pass the slug you think is likely; the tool "
        "tries a handful of heuristics if the first one 404s."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "description": "Company name (used for snapshot keying + "
                               "heuristic slug fallbacks).",
            },
            "ats_slug": {
                "type": "string",
                "description": "The ATS-specific slug (e.g. 'alphasense' for "
                               "Greenhouse). If omitted, the tool tries "
                               "company-name-derived heuristics.",
            },
            "provider": {
                "type": "string",
                "enum": ["greenhouse"],
                "description": "ATS provider. Only 'greenhouse' supported today.",
                "default": "greenhouse",
            },
        },
        "required": ["company"],
    },
}


# ============================================================================
# Tool implementation
# ============================================================================

@dataclass
class ATSFetcherTool:
    """Direct ATS API client + snapshot diff. One instance per task."""
    snapshot_store: ATSSnapshotStore = field(default_factory=ATSSnapshotStore)
    _count: int = 0
    _observed_urls: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return ATS_FETCHER_SCHEMA["name"]

    @property
    def description(self) -> str:
        return ATS_FETCHER_SCHEMA["description"]

    @property
    def input_schema(self) -> dict[str, Any]:
        return ATS_FETCHER_SCHEMA["input_schema"]

    @property
    def call_count(self) -> int:
        return self._count

    @property
    def observed_urls(self) -> list[str]:
        return list(self._observed_urls)

    def __call__(
        self,
        company: str,
        ats_slug: str | None = None,
        provider: str = "greenhouse",
    ) -> str:
        self._count += 1

        if provider != "greenhouse":
            return (
                f"ERROR: provider={provider!r} not supported. "
                "Only 'greenhouse' is implemented today."
            )

        # Try the model-supplied slug first, then heuristics.
        candidates: list[str] = []
        if ats_slug:
            candidates.append(ats_slug.strip().lower())
        candidates.extend(_slug_heuristics(company))
        # De-dup while preserving order.
        seen: set[str] = set()
        candidates = [s for s in candidates if not (s in seen or seen.add(s))]

        jobs: list[dict[str, Any]] | None = None
        resolved_slug: str | None = None
        last_error: str | None = None
        for slug in candidates:
            try:
                jobs = _fetch_greenhouse(slug)
                resolved_slug = slug
                break
            except _NotFoundError:
                last_error = f"slug={slug!r} → 404"
                continue
            except Exception as e:
                last_error = f"slug={slug!r} → {type(e).__name__}: {e}"
                continue

        if jobs is None or resolved_slug is None:
            return (
                f"NO_ATS_MATCH: could not find a Greenhouse board for "
                f"{company} (tried slugs: {', '.join(candidates) or '(none)'}). "
                f"Last error: {last_error or 'all 404'}. "
                "The company may not use Greenhouse; fall back to "
                "hiring_signals output."
            )

        # Persist a snapshot + diff against the most recent prior snapshot.
        previous = self.snapshot_store.load_latest(
            company=company, provider=provider,
        )
        prev_titles_norm = (
            {_normalize_title(t) for t in (previous.get("titles") or [])}
            if previous else set()
        )
        # Closures = was in prev, not in current. Surface ALL closed titles to
        # the model — it can decide which are interesting.
        closed_titles = sorted(
            prev_titles_norm - {_normalize_title(j["title"]) for j in jobs}
        )

        # Surface job URLs so eval source-check can verify them later.
        for j in jobs:
            url = j.get("absolute_url") or ""
            if url and url not in self._observed_urls:
                self._observed_urls.append(url)

        # Important-role classification on current open jobs.
        important_open: list[dict[str, Any]] = []
        for j in jobs:
            title = j.get("title", "")
            classification = classify_important_title(title)
            if classification is None:
                continue
            tier_id, tier_desc = classification
            important_open.append({
                "title": title,
                "location": _location_string(j.get("location")),
                "url": j.get("absolute_url", ""),
                "tier": tier_id,
                "tier_description": tier_desc,
                "updated_at": j.get("updated_at"),
            })

        # Important closures: cross-reference closed titles back through the
        # classifier. We only have the title here (no URL/location, since the
        # role is no longer in the live data).
        important_closed: list[dict[str, Any]] = []
        for normed in closed_titles:
            # Find the original (un-normalized) title from the previous
            # snapshot for human-readable rendering.
            original = next(
                (t for t in (previous.get("titles") or [])
                 if _normalize_title(t) == normed),
                normed,
            )
            classification = classify_important_title(original)
            if classification is None:
                continue
            tier_id, tier_desc = classification
            important_closed.append({
                "title": original,
                "tier": tier_id,
                "tier_description": tier_desc,
            })

        # Persist this run's snapshot. Done AFTER the diff so the diff is
        # against the prior run, not against itself.
        self.snapshot_store.store(
            company=company,
            provider=provider,
            slug=resolved_slug,
            jobs=jobs,
        )

        return _render(
            company=company,
            slug=resolved_slug,
            total=len(jobs),
            jobs=jobs,
            important_open=important_open,
            important_closed=important_closed,
            previous_snapshot_at=previous.get("snapshot_at") if previous else None,
        )


# ============================================================================
# Greenhouse client
# ============================================================================

class _NotFoundError(Exception):
    """Raised when Greenhouse responds 404 for a slug — try the next one."""


def _fetch_greenhouse(slug: str) -> list[dict[str, Any]]:
    url = f"{GREENHOUSE_API_BASE}/{slug}/jobs"
    with GREENHOUSE_LIMITER:
        resp = httpx.get(url, timeout=30)
    if resp.status_code == 404:
        raise _NotFoundError(slug)
    resp.raise_for_status()
    data = resp.json()
    return data.get("jobs", []) or []


def _slug_heuristics(company: str) -> list[str]:
    """Generate plausible Greenhouse slug guesses from a company name.

    Greenhouse slugs are typically lowercased, hyphen-or-no-separator. We try
    a few common transformations; the caller iterates until one returns 200.
    """
    if not company:
        return []
    base = re.sub(r"[^\w\s\-]", "", company).strip().lower()
    candidates: list[str] = [
        base.replace(" ", ""),
        base.replace(" ", "-"),
        base.split()[0] if " " in base else base,
    ]
    return [c for c in candidates if c]


# ============================================================================
# Rendering + utilities
# ============================================================================

_NORMALISE_RE = re.compile(r"\s+")


def _normalize_title(title: str) -> str:
    """Trim, lowercase, collapse whitespace. Used as the dedup key for diffs.
    Two postings with the same title but different locations count as one
    role for closure detection."""
    return _NORMALISE_RE.sub(" ", (title or "").strip()).lower()


def _location_string(loc: Any) -> str:
    if isinstance(loc, dict):
        return loc.get("name", "") or ""
    return str(loc) if loc else ""


def _render(
    *, company: str, slug: str, total: int, jobs: list[dict[str, Any]],
    important_open: list[dict[str, Any]],
    important_closed: list[dict[str, Any]],
    previous_snapshot_at: str | None,
) -> str:
    """Compose the text payload returned to the model. Structured but easy to
    scan; includes the important-role flags at the top so the model sees
    them before the long title list."""
    lines: list[str] = [
        "ATS provider: greenhouse",
        f"Slug: {slug}",
        f"Company: {company}",
        f"Total open roles: {total}",
    ]
    if previous_snapshot_at:
        lines.append(f"Previous snapshot: {previous_snapshot_at}")
    else:
        lines.append("Previous snapshot: (none — this is the first run)")

    lines.append("")
    lines.append(f"Important roles currently open ({len(important_open)}):")
    if important_open:
        for r in important_open[:30]:
            lines.append(
                f"  - [{r['tier']}] {r['title']} ({r['location']})\n"
                f"      {r['url']}"
            )
    else:
        lines.append("  (none flagged)")

    lines.append("")
    lines.append(
        f"Important roles closed since previous snapshot "
        f"({len(important_closed)}):"
    )
    if important_closed:
        for r in important_closed[:30]:
            lines.append(f"  - [{r['tier']}] {r['title']}")
    else:
        lines.append("  (none — or no previous snapshot to diff against)")

    # Full title list (capped) for the model's general awareness.
    lines.append("")
    lines.append(f"All titles ({total}):")
    by_loc: dict[str, list[str]] = {}
    for j in jobs:
        title = j.get("title", "?")
        loc = _location_string(j.get("location")) or "(no location)"
        by_loc.setdefault(loc, []).append(title)
    shown = 0
    for loc, titles in sorted(by_loc.items()):
        if shown >= 60:
            lines.append(f"  ... and {total - shown} more")
            break
        lines.append(f"  [{loc}]")
        for t in titles:
            if shown >= 60:
                break
            lines.append(f"    - {t}")
            shown += 1

    return "\n".join(lines)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
