"""ApifyAdScraperTool — wraps Apify actors that scrape LinkedIn, Meta, and TikTok
ad libraries for a target company.

One Tool instance per task call → per-task counter for cost tracking. Each
actor invocation is cached by (platform, company, country, max_results) so a
rerun within the cache TTL returns the previous payload at zero cost — ad
libraries don't change second-to-second, and Apify call cost is per-result.

Cache TTLs (longer than Tavily's 24h because ad libraries are slow-moving):
- successful runs: 7 days
- transient/retryable errors: 30 minutes (Fix Appendix #9 — don't poison cache)

Actor pricing (as of 2026-05-11; verify periodically in the Apify console):
- LinkedIn (`automation-lab/linkedin-ad-library-scraper`): free
- Meta (`automly/facebook-ad-library-scraper`): ~$0.65 / 1k results
- TikTok (`apify/tiktok-ads-scraper`): pay-per-result tier

`APIFY_API_KEY` is optional. When absent, the tool returns a structured
"not configured" message so the model can still complete the task with
confidence="low" rather than hard-erroring the whole account.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

try:
    from apify_client import ApifyClient
    from apify_client.errors import ApifyApiError
except ImportError:  # pragma: no cover — only needed for live runs
    ApifyClient = None  # type: ignore
    ApifyApiError = Exception  # type: ignore

import config
from rate_limit import APIFY_LIMITER
from run_log import ToolCallCache


# Bumped when the cache payload shape changes OR when an actor's input shape
# changes (so old cached "successes" that were actually no-ops because of a
# wrong input shape don't keep being served).
# v2: corrected LinkedIn input (searchQuery + dateRange) + TikTok actor swap.
# v3: format/URL extractors recognise the actor field names (adFormat label,
#     detailUrl, mediaUrl, etc.). Cached v1/v2 renders had "unknown: N" mix.
APIFY_TOOL_VERSION = "apify_ad_scraper_v3"

# Actor IDs. Constants so swapping a scraper is a one-line change.
# TikTok: there is no official Apify-maintained TikTok Ad Library actor — we use
# the community actor with the most runs as of 2026-05-11 (ivanvs, 6.1k runs).
# It accepts pre-constructed library URLs only, so _build_actor_input synthesises
# the URL from the company name + country code.
ACTOR_IDS: dict[str, str] = {
    "linkedin": "automation-lab/linkedin-ad-library-scraper",
    "meta":     "automly/facebook-ad-library-scraper",
    "tiktok":   "ivanvs/tiktok-ad-library-scraper",
}

SUPPORTED_PLATFORMS = tuple(ACTOR_IDS.keys())


APIFY_AD_SCRAPER_SCHEMA: dict[str, Any] = {
    "name": "apify_ad_scraper",
    "description": (
        "Look up which ads a company is currently running on LinkedIn, Meta "
        "(Facebook/Instagram), or TikTok ad libraries. Returns count + format "
        "mix (static / motion / video / carousel) + a few example ad URLs. "
        "Calling rules:\n"
        "1. ALWAYS call platform='linkedin' first — every B2B account uses LinkedIn.\n"
        "2. Call platform='meta' ONLY if the company is B2C / DTC / hybrid AND "
        "LinkedIn returned ads_running > 0. Routine pure-B2B companies have "
        "near-zero Meta presence and the Meta scraper costs ~$0.65/1k results.\n"
        "3. Call platform='tiktok' ONLY if the company targets Gen-Z / lifestyle / "
        "consumer creators AND LinkedIn returned ads_running > 0.\n"
        "Do NOT call meta or tiktok for B2B-only enterprise software companies."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "platform": {
                "type": "string",
                "enum": list(SUPPORTED_PLATFORMS),
                "description": "Which ad library to query.",
            },
            "company": {
                "type": "string",
                "description": "Company name as it appears in the ad library "
                               "(usually the brand name, not the legal entity).",
            },
            "country": {
                "type": "string",
                "description": "Two-letter ISO country code. Default 'US'. "
                               "LinkedIn supports global; Meta and TikTok are "
                               "country-scoped.",
                "default": "US",
            },
            "max_results": {
                "type": "integer",
                "description": (
                    f"Cap on returned ads. Default 25. Hard ceiling "
                    f"{config.MAX_APIFY_RESULTS_PER_PLATFORM}."
                ),
                "default": 25,
            },
        },
        "required": ["platform", "company"],
    },
}


@dataclass
class ApifyAdScraperTool:
    """Apify actor wrapper with cache + per-task hard cap + observed-URL ledger."""
    client: Any = None  # ApifyClient — late-bound in __post_init__ when key exists
    cache: ToolCallCache = field(default_factory=ToolCallCache)
    _count: int = 0
    _cache_hits: int = 0
    _observed_urls: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.client is None and config.APIFY_API_KEY and ApifyClient is not None:
            self.client = ApifyClient(token=config.APIFY_API_KEY)

    @property
    def name(self) -> str:
        return APIFY_AD_SCRAPER_SCHEMA["name"]

    @property
    def description(self) -> str:
        return APIFY_AD_SCRAPER_SCHEMA["description"]

    @property
    def input_schema(self) -> dict[str, Any]:
        return APIFY_AD_SCRAPER_SCHEMA["input_schema"]

    @property
    def call_count(self) -> int:
        return self._count

    @property
    def cache_hit_count(self) -> int:
        return self._cache_hits

    @property
    def observed_urls(self) -> list[str]:
        return list(self._observed_urls)

    def _record_urls(self, urls: list[str]) -> None:
        for u in urls:
            if u and u not in self._observed_urls:
                self._observed_urls.append(u)

    def __call__(
        self,
        platform: str,
        company: str,
        country: str = "US",
        max_results: int = 25,
    ) -> str:
        # Hard cap pre-call (Fix Appendix #10).
        if self._count >= config.APIFY_CALLS_PER_TASK_CAP:
            return (
                f"ERROR: apify cap reached for this task "
                f"({config.APIFY_CALLS_PER_TASK_CAP}). "
                "Produce best-effort output with the platforms you've already "
                "queried and set confidence accordingly."
            )

        if platform not in SUPPORTED_PLATFORMS:
            return (
                f"ERROR: unknown platform {platform!r}. "
                f"Supported: {', '.join(SUPPORTED_PLATFORMS)}."
            )

        # Clamp max_results to the configured ceiling.
        max_results = max(1, min(int(max_results), config.MAX_APIFY_RESULTS_PER_PLATFORM))

        self._count += 1
        args: dict[str, Any] = {
            "platform": platform,
            "company": company,
            "country": country,
            "max_results": max_results,
            "tool_version": APIFY_TOOL_VERSION,
        }

        # Cache lookup first.
        cached = self.cache.lookup(self.name, args)
        if cached is not None:
            self._cache_hits += 1
            self._record_urls(_extract_urls(cached))
            return cached

        # Graceful degrade if Apify isn't wired up yet — let the task still
        # produce a valid (low-confidence) output rather than hard-failing.
        if self.client is None:
            text = (
                f"NOT CONFIGURED: APIFY_API_KEY missing. "
                f"Cannot query {platform} ads for {company}. "
                "Treat as ads_running=0 with confidence='low' and note that "
                "ad-library data is unavailable for this run."
            )
            # No cache — once Apify is configured we want immediate live data.
            return text

        actor_id = ACTOR_IDS[platform]
        run_input = _build_actor_input(platform, company, country, max_results)
        try:
            with APIFY_LIMITER:
                run = self.client.actor(actor_id).call(
                    run_input=run_input,
                    timeout_secs=300,
                )
                dataset_id = run.get("defaultDatasetId") if run else None
                items: list[dict[str, Any]] = []
                if dataset_id:
                    items = list(
                        self.client.dataset(dataset_id).iterate_items()
                    )[:max_results]
        except ApifyApiError as e:
            status = getattr(e, "status_code", None)
            if status in (408, 425, 429, 500, 502, 503, 504):
                text = f"ERROR: retryable apify failure: {status} on {platform}"
                self.cache.store(self.name, args, text, ttl_minutes=30)
                return text
            return f"ERROR: apify failed ({platform}): {type(e).__name__}: {e}"
        except Exception as e:  # network / timeout / unexpected
            text = f"ERROR: retryable apify failure: {type(e).__name__} on {platform}"
            self.cache.store(self.name, args, text, ttl_minutes=30)
            return text

        text = _render_results(platform, company, country, items)
        self.cache.store(self.name, args, text, ttl_hours=24 * 7)  # 7-day cache
        self._record_urls(_extract_urls(text))
        return text


# ---- per-actor input shapes ----

def _build_actor_input(
    platform: str, company: str, country: str, max_results: int,
) -> dict[str, Any]:
    """Translate the generic tool args into the actor's expected input shape.

    Each Apify actor accepts a different schema; centralised here so swapping
    an actor (e.g. Meta switching scrapers) is a one-place change. Input shapes
    captured 2026-05-11 from each actor's published build inputSchema.
    """
    if platform == "linkedin":
        # `automation-lab/linkedin-ad-library-scraper` — searchQuery + dateRange.
        # Spec §10 says 12-month window, so `past-year` rather than `past-month`.
        return {
            "searchQuery": company,
            "maxAds": max_results,
            "dateRange": "past-year",
            "adFormat": "all",
        }
    if platform == "meta":
        # `automly/facebook-ad-library-scraper` — searchTerms[] + country + maxAds.
        return {
            "searchTerms": [company],
            "country": country.upper(),
            "maxAds": max_results,
            "activeStatus": "active",
        }
    if platform == "tiktok":
        # `ivanvs/tiktok-ad-library-scraper` accepts library URLs only — we
        # synthesise the TikTok Ad Library search URL from the company name.
        from urllib.parse import quote_plus
        adv_name = quote_plus(company)
        region = country.upper()
        url = (
            f"https://library.tiktok.com/ads?region={region}"
            f"&adv_name={adv_name}&query_type=2&sort_type=last_shown_date,desc"
        )
        return {
            "urls": [{"url": url}],
            "maxRecords": max_results,
        }
    raise ValueError(f"Unsupported platform: {platform}")


# ---- result rendering ----

# Maps actor-side type strings → our canonical format vocabulary.
# Each actor returns its own naming; we normalise so the rendered output has a
# single shape regardless of source. Keys are lowercased; partial-match keys
# (e.g. "image" → "static") are tried after exact matches.
_FORMAT_EXACT: dict[str, str] = {
    "image": "static", "static": "static", "photo": "static",
    "video": "video", "motion": "motion",
    "carousel": "carousel", "slideshow": "carousel",
    "single_image": "static", "single_video": "video",
    "single image ad": "static", "video ad": "video",
    "carousel ad": "carousel", "spotlight ad": "static",
    "text ad": "text", "follower ad": "static",
    "message ad": "static", "conversation ad": "static",
    "document ad": "document", "event ad": "static",
    "thought leader ad": "static",
}

_FORMAT_SUBSTRING_RULES: list[tuple[str, str]] = [
    ("video", "video"), ("carousel", "carousel"),
    ("image", "static"), ("photo", "static"),
    ("document", "document"), ("text", "text"),
]


def _normalise_format(raw: str | None) -> str:
    if not raw:
        return "unknown"
    key = str(raw).lower().strip()
    if key in _FORMAT_EXACT:
        return _FORMAT_EXACT[key]
    for needle, canonical in _FORMAT_SUBSTRING_RULES:
        if needle in key:
            return canonical
    return key  # last-resort: hand the actor's label through unchanged


def _extract_format(item: dict[str, Any]) -> str:
    # Field names span actors: adFormat (LinkedIn), media_type / mediaType (Meta),
    # creative_type, type, format.
    for key in ("adFormat", "ad_format", "media_type", "mediaType",
                "ad_type", "format", "creative_type", "type"):
        if key in item and item[key]:
            return _normalise_format(str(item[key]))
    if item.get("videoUrl") or item.get("video_url") or item.get("videoDuration"):
        return "video"
    if item.get("carouselCards") or item.get("carousel") or item.get("cards"):
        return "carousel"
    if item.get("imageUrl") or item.get("image_url") or item.get("mediaUrl") or item.get("image"):
        return "static"
    return "unknown"


def _extract_url(item: dict[str, Any]) -> str | None:
    # Actors use a mix: detailUrl (LinkedIn), permalink (Meta), ad_url, url.
    for key in ("detailUrl", "ad_url", "permalink", "ad_permalink",
                "url", "link", "ctaUrl"):
        v = item.get(key)
        if v:
            return str(v)
    return None


def _bucket_volume(count: int) -> str:
    """Categorise raw ad count into Low / Medium / High for the page body."""
    if count <= 0:
        return "none"
    if count <= 10:
        return "low"
    if count <= 50:
        return "medium"
    return "high"


def _render_results(
    platform: str, company: str, country: str, items: list[dict[str, Any]],
) -> str:
    if not items:
        return (
            f"Platform: {platform}\n"
            f"Company: {company}\n"
            f"Country: {country}\n"
            f"ads_running: 0\n"
            f"volume: none\n"
            f"format_mix: (none)\n"
        )

    formats = Counter(_extract_format(i) for i in items)
    urls = [_extract_url(i) for i in items]
    urls_clean = [u for u in urls if u]

    format_lines = [f"  - {fmt}: {n}" for fmt, n in formats.most_common()]
    sample_urls = "\n".join(f"  - {u}" for u in urls_clean[:5])
    return (
        f"Platform: {platform}\n"
        f"Company: {company}\n"
        f"Country: {country}\n"
        f"ads_running: {len(items)}\n"
        f"volume: {_bucket_volume(len(items))}\n"
        f"format_mix:\n" + "\n".join(format_lines) + "\n"
        f"sample_urls:\n{sample_urls or '  (none)'}\n"
    )


def _extract_urls(rendered_text: str) -> list[str]:
    """Pull URLs out of a rendered cache payload."""
    urls: list[str] = []
    for line in rendered_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- http"):
            urls.append(stripped[2:].strip())
    return urls
