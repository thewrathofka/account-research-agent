"""Deterministic LinkedIn-company-slug discovery.

Background. `research_pass.linkedin_company_url` is captured by the LLM during
its broad-research pass. Live evidence in runs.db (2026-05-12 → 2026-05-17) shows
Sonnet 4.6 non-deterministically hallucinating LinkedIn slugs when web-search
results are ambiguous — for Miro, three different runs returned `NULL`,
`/company/miro/` (wrong magazine company), and `/company/mirohq/` (correct).
M2 and M10 both consume this field and inherit the failure.

This helper replaces the LLM's guess with a deterministic Tavily-based
discovery step. One search + a deterministic scoring algorithm picks the best
candidate. Falls back to the LLM's hint when Tavily fails, and to no-URL when
neither is available.

Contract:
- `discover_linkedin_company_url(company, hint_url, web_search_tool)` returns
  a `SlugDiscovery` dataclass. Callers pass `discovery.url` to Apify.
- `discovery.confidence < 0.5` means top candidates were close-scored. The
  caller may still proceed (Apify gets the top scorer) but should surface
  `slug_ambiguous=True` so the orchestrator's confidence aggregator routes
  the account to `needs_review` rather than locking in a wrong gate decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tools.hiring_signals import normalize_company
from tools.web_search import tavily_quota_exhausted


# Matches `(https://)(?:[lang].)?linkedin.com/company/<slug>` with optional
# trailing slash, path, or query. Slug allowed chars match LinkedIn's actual
# constraints: lowercase alphanumeric + hyphen, first char must be alphanum.
LINKEDIN_COMPANY_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/([a-z0-9][a-z0-9\-]+)/?",
    re.IGNORECASE,
)

# Snippet-content keywords that indicate a result is a real LinkedIn company
# page (rich metadata) rather than a user profile or news article mentioning
# the company in passing. Tuned from observed Tavily output on company-page
# results, which canonically include 3+ of these in the rendered snippet.
_COMPANY_PAGE_KEYWORDS = (
    "employees", "founded", "headquarters", "industry",
    "specialties", "followers", "based in",
)

# Common slug variants companies use when their bare name is taken on LinkedIn:
# {name}hq, {name}app, {name}co, {name}inc, {name}io, {name}dev. Miro's
# `mirohq` is the canonical example.
_SLUG_VARIANT_SUFFIXES = ("hq", "app", "co", "inc", "io", "dev", "labs")

# Follower / employee count regex for the rare close-tie tiebreak. Matches
# "1,234 employees", "1.2M followers", etc. We extract the leading number to
# compare magnitudes — a stale company page has 0 followers; the active one
# has thousands.
_COUNT_RE = re.compile(
    r"(\d[\d,\.]*)\s*([kKmM]?)\s*(employees|followers)",
    re.IGNORECASE,
)

# Below this confidence the caller should mark the result as ambiguous so the
# orchestrator's confidence aggregator can route to `needs_review` instead of
# silently locking in a wrong gate decision.
LOW_CONFIDENCE_THRESHOLD = 0.5


@dataclass(frozen=True)
class SlugDiscovery:
    """Outcome of a slug-discovery attempt.

    url: canonical https://www.linkedin.com/company/<slug>/ URL, or None.
    source: provenance of the URL:
      - 'tavily_exact':   short-circuited on a high-confidence Tavily result
      - 'tavily_scored':  picked by the scoring algorithm
      - 'hint':           Tavily found nothing usable, fell back to caller-provided URL
      - 'hint_quota_exhausted': Tavily quota exhausted, fell back to hint
      - 'name_fallback':  neither Tavily nor hint produced a URL — let downstream do name search
    confidence: 0.0–1.0. < LOW_CONFIDENCE_THRESHOLD means the top candidates
      were close-scored and the caller should treat the result as soft.
    candidates_scored: top 3 (url, score) tuples for debugability / runs.db.
    """
    url: str | None
    source: str
    confidence: float
    candidates_scored: list[tuple[str, int]] = field(default_factory=list)


def _slug_from_url(url: str) -> str | None:
    """Pull the lowercased slug out of a LinkedIn company URL, or None."""
    m = LINKEDIN_COMPANY_RE.search(url)
    return m.group(1).lower() if m else None


def _canonicalize(url: str) -> str | None:
    """Rewrite a matched URL to a stable form: `https://www.linkedin.com/company/<slug>/`."""
    slug = _slug_from_url(url)
    return f"https://www.linkedin.com/company/{slug}/" if slug else None


def _company_signal_count(blob: str) -> int:
    """Number of distinct company-page keywords present in a title+snippet blob."""
    b = blob.lower()
    return sum(1 for kw in _COMPANY_PAGE_KEYWORDS if kw in b)


def _normalize_token(company: str) -> str:
    """Squash normalize_company() output to a single token (no spaces, no hyphens).

    `normalize_company("Stripe, Inc.")` → "stripe" already; this collapses
    multi-word names like "Alpha Sense" → "alphasense" so they can be slug-
    compared against `/company/alphasense/`.
    """
    return normalize_company(company).replace(" ", "").replace("-", "")


def _kebab_token(company: str) -> str:
    """Hyphenated form of the normalized name, for `/company/alpha-sense/` matches."""
    return normalize_company(company).replace(" ", "-").replace("--", "-")


def _parse_count(blob: str) -> int:
    """Extract the highest numeric employee/follower count from a snippet.

    Used as a tiebreaker when two candidates score within 15 points. The
    stale Miro page typically has zero followers in its snippet; the real
    mirohq has 1M+. Returns 0 when no count is found.
    """
    best = 0
    for match in _COUNT_RE.finditer(blob):
        raw, suffix, _label = match.groups()
        try:
            n = float(raw.replace(",", ""))
        except ValueError:
            continue
        if suffix.lower() == "k":
            n *= 1_000
        elif suffix.lower() == "m":
            n *= 1_000_000
        best = max(best, int(n))
    return best


def _parse_tavily_results(rendered_text: str) -> list[dict[str, str]]:
    """Parse the WebSearchTool's rendered text payload back into structured results.

    WebSearchTool returns a multi-section string formatted as
    `Result N:\nTitle: ...\nURL: ...\nContent: ...`. We split it back so
    discovery scoring can operate on (title, url, snippet) per result.
    """
    results: list[dict[str, str]] = []
    blocks = re.split(r"\n\nResult \d+:\n", "\n\n" + rendered_text)
    for block in blocks:
        if not block.strip():
            continue
        if block.startswith("Result"):
            block = re.sub(r"^Result \d+:\n", "", block)
        title = url = content = ""
        for line in block.split("\n"):
            if line.startswith("Title: "):
                title = line[len("Title: "):]
            elif line.startswith("URL: "):
                url = line[len("URL: "):]
            elif line.startswith("Content: "):
                content = line[len("Content: "):]
        if url:
            results.append({"title": title, "url": url, "content": content})
    return results


def _score_candidate(
    *,
    url: str,
    title: str,
    snippet: str,
    company: str,
    position: int,
    dup_count: int,
) -> int:
    """Score a single LinkedIn URL candidate. Higher = more likely the right slug.

    See the scoring table in plan §Approach for the rubric and the Miro / Stripe
    walkthroughs that validate it.
    """
    slug = _slug_from_url(url) or ""
    name_token = _normalize_token(company)
    name_kebab = _kebab_token(company)
    blob = f"{title} {snippet}".lower()
    score = 0

    if slug == name_token:
        score += 60
    elif slug in {f"{name_token}{sfx}" for sfx in _SLUG_VARIANT_SUFFIXES}:
        score += 40
    elif slug == name_kebab:
        score += 25
    elif slug.startswith(name_token) or name_token.startswith(slug):
        score += 15

    signals = _company_signal_count(blob)
    if signals >= 3:
        score += 30
    elif signals >= 1:
        score += 10 * signals

    score += max(0, (5 - position)) * 5
    score += max(0, dup_count - 1) * 20

    return score


def discover_linkedin_company_url(
    company: str,
    hint_url: str | None,
    web_search_tool: Any,
) -> SlugDiscovery:
    """Find the canonical LinkedIn company URL for `company`.

    Pipeline:
    1. If Tavily quota is exhausted, skip search and degrade to hint.
    2. Issue one Tavily search: `"<company>" linkedin company`.
    3. Extract LinkedIn company URLs from the rendered results.
    4. Short-circuit: result-1 has slug exact match AND ≥3 company-page
       keywords → return immediately with confidence 0.95.
    5. Otherwise score each unique candidate; pick the top scorer. When the
       top two score within 15 points, tiebreak by follower/employee count.
    6. Fallback ladder: scored Tavily winner → hint_url → None.

    The caller (Apify tool) is responsible for passing `url` into the actor
    and echoing `source`/`confidence`/`candidates_scored` into its payload.
    """
    candidates_seen: list[tuple[str, int]] = []

    if tavily_quota_exhausted():
        return SlugDiscovery(
            url=_canonicalize(hint_url) if hint_url else None,
            source="hint_quota_exhausted" if hint_url else "name_fallback",
            confidence=0.3 if hint_url else 0.0,
            candidates_scored=candidates_seen,
        )

    query = f'"{company}" linkedin company'
    try:
        rendered = web_search_tool(query)
    except Exception:
        rendered = None

    if not rendered or (isinstance(rendered, str) and rendered.startswith("ERROR")):
        return SlugDiscovery(
            url=_canonicalize(hint_url) if hint_url else None,
            source="hint" if hint_url else "name_fallback",
            confidence=0.3 if hint_url else 0.0,
            candidates_scored=candidates_seen,
        )

    parsed = _parse_tavily_results(rendered)
    if not parsed:
        return SlugDiscovery(
            url=_canonicalize(hint_url) if hint_url else None,
            source="hint" if hint_url else "name_fallback",
            confidence=0.3 if hint_url else 0.0,
            candidates_scored=candidates_seen,
        )

    by_slug: dict[str, list[tuple[int, dict[str, str]]]] = {}
    for pos, result in enumerate(parsed, start=1):
        slug = _slug_from_url(result["url"])
        if not slug:
            continue
        by_slug.setdefault(slug, []).append((pos, result))

    if not by_slug:
        return SlugDiscovery(
            url=_canonicalize(hint_url) if hint_url else None,
            source="hint" if hint_url else "name_fallback",
            confidence=0.3 if hint_url else 0.0,
            candidates_scored=candidates_seen,
        )

    name_token = _normalize_token(company)
    first = parsed[0]
    first_slug = _slug_from_url(first["url"])
    if first_slug == name_token:
        first_blob = f"{first['title']} {first['content']}"
        if _company_signal_count(first_blob) >= 3:
            url = _canonicalize(first["url"])
            return SlugDiscovery(
                url=url,
                source="tavily_exact",
                confidence=0.95,
                candidates_scored=[(url or "", 9999)],
            )

    scored: list[tuple[int, str, dict[str, str], int]] = []
    for slug, hits in by_slug.items():
        pos, result = min(hits, key=lambda h: h[0])
        s = _score_candidate(
            url=result["url"],
            title=result["title"],
            snippet=result["content"],
            company=company,
            position=pos,
            dup_count=len(hits),
        )
        scored.append((s, slug, result, pos))

    scored.sort(key=lambda x: x[0], reverse=True)
    candidates_seen = [(_canonicalize(r["url"]) or r["url"], sc) for sc, _, r, _ in scored[:3]]

    top_score, top_slug, top_result, _top_pos = scored[0]
    winner_url = _canonicalize(top_result["url"])
    confidence = min(0.9, top_score / 130.0)

    if len(scored) >= 2:
        runner_score, _, runner_result, _ = scored[1]
        if (top_score - runner_score) <= 15:
            top_count = _parse_count(f"{top_result['title']} {top_result['content']}")
            runner_count = _parse_count(f"{runner_result['title']} {runner_result['content']}")
            if runner_count > top_count and runner_count > 0:
                winner_url = _canonicalize(runner_result["url"])
                confidence = max(0.4, confidence - 0.1)
            else:
                confidence = max(0.4, confidence - 0.15)

    return SlugDiscovery(
        url=winner_url,
        source="tavily_scored",
        confidence=confidence,
        candidates_scored=candidates_seen,
    )
