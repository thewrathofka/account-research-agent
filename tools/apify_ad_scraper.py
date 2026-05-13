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
    _APIFY_AVAILABLE = True
except ImportError:  # pragma: no cover — only needed for live runs
    ApifyClient = None  # type: ignore
    # Sentinel class so `except ApifyApiError` doesn't degrade to a catch-all
    # Exception when the apify-client package is absent. This branch only
    # fires when the tool runs without the dependency installed (e.g. unit
    # tests with monkeypatched HTTP); we never actually reach the except
    # clause because ApifyClient is None and we short-circuit earlier.
    class ApifyApiError(Exception):  # type: ignore
        status_code: int | None = None
    _APIFY_AVAILABLE = False

import config
from rate_limit import APIFY_LIMITER
from run_log import ToolCallCache


# Bumped when the cache payload shape changes OR when an actor's input shape
# changes (so old cached "successes" that were actually no-ops because of a
# wrong input shape don't keep being served).
# v2: corrected LinkedIn input (searchQuery + dateRange) + TikTok actor swap.
# v3: format/URL extractors recognise the actor field names (adFormat label,
#     detailUrl, mediaUrl, etc.). Cached v1/v2 renders had "unknown: N" mix.
# v4: canonical-URL inputs (company_url / page_url / tiktok_handle) override
#     free-text searchQuery when present + advertiser-name fuzzy post-filter.
#     Cached v3 entries had ad-noise from name-collision matches (Apr-May 2026).
# v5: canonical URL no longer overrides searchQuery — passing a URL as
#     searchQuery to LinkedIn's actor over-restricted (Oracle returned 0).
#     Free-text query stays for breadth; canonical URL is now a FILTER
#     BOOSTER: items whose advertiser-page URL matches the canonical URL
#     bypass the name-similarity threshold. Catches Oracle (broad ads) AND
#     filters WIRED (name-collision noise).
APIFY_TOOL_VERSION = "apify_ad_scraper_v5"

# Token-set Jaccard threshold for advertiser-name matching. 0.5 catches
# "Stripe" vs "Stripe, Inc." but rejects "Stripe" vs "Stripe Investments"
# (which shares only the "stripe" token). Tuned for false-positive guard
# duty — too strict and we'd drop "WIRED" (the magazine) vs "WIRED Media".
_ADVERTISER_MATCH_THRESHOLD = 0.5

# Suffix tokens stripped before similarity computation. The legal forms drift
# across geographies but the marketing brand stays the same.
_COMPANY_SUFFIX_TOKENS = {
    "inc", "inc.", "incorporated", "corp", "corp.", "corporation",
    "ltd", "ltd.", "limited", "llc", "l.l.c.", "plc", "co", "co.",
    "company", "gmbh", "sa", "s.a.", "ag", "bv", "b.v.", "kk", "k.k.",
    "pte", "pty", "the",
}

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
        "mix (static / motion / video / carousel) + a few example ad URLs.\n"
        "ACCURACY: pass the canonical platform URL/handle whenever possible "
        "(linkedin_company_url, facebook_page_url, tiktok_handle) — these come "
        "from research_pass and eliminate name-collision noise. Without them "
        "the tool falls back to free-text search + advertiser-name post-filter "
        "(which catches most but not all bad matches).\n"
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
                               "(usually the brand name, not the legal entity). "
                               "Used for free-text fallback AND as the input to "
                               "the advertiser-name post-filter.",
            },
            "linkedin_company_url": {
                "type": "string",
                "description": (
                    "Canonical LinkedIn company URL of form "
                    "https://www.linkedin.com/company/<slug>/. When provided "
                    "with platform='linkedin', the tool queries the company "
                    "ad-library page directly instead of free-text search. "
                    "Copy this from research_pass.linkedin_company_url."
                ),
            },
            "facebook_page_url": {
                "type": "string",
                "description": (
                    "Canonical Facebook page URL of form "
                    "https://www.facebook.com/<slug>. When provided with "
                    "platform='meta', queried as a page-anchored search "
                    "instead of free-text. Copy from "
                    "research_pass.facebook_page_url."
                ),
            },
            "tiktok_handle": {
                "type": "string",
                "description": (
                    "TikTok handle with leading `@` (e.g. `@miro`). When "
                    "provided with platform='tiktok', queried as a handle-"
                    "anchored search instead of brand-name search. Copy from "
                    "research_pass.tiktok_handle."
                ),
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
        linkedin_company_url: str | None = None,
        facebook_page_url: str | None = None,
        tiktok_handle: str | None = None,
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
        # Canonical-URL inputs participate in the cache key so a per-platform
        # canonical URL doesn't collide with a free-text run of the same brand
        # (their result sets differ; we don't want the cache to confuse them).
        args: dict[str, Any] = {
            "platform": platform,
            "company": company,
            "country": country,
            "max_results": max_results,
            "tool_version": APIFY_TOOL_VERSION,
            "linkedin_company_url": linkedin_company_url,
            "facebook_page_url": facebook_page_url,
            "tiktok_handle": tiktok_handle,
        }

        # Graceful degrade if Apify isn't wired up yet — checked BEFORE the
        # cache lookup so a degraded run is honest about its state. Otherwise
        # a stale cache hit from a previously-keyed run would mask the missing
        # key, and the agent would silently produce confident output from
        # week-old data.
        if self.client is None:
            return (
                f"NOT CONFIGURED: APIFY_API_KEY missing. "
                f"Cannot query {platform} ads for {company}. "
                "Treat as ads_running=0 with confidence='low' and note that "
                "ad-library data is unavailable for this run."
            )

        # Cache lookup first (we have a client → cached data is trustworthy).
        cached = self.cache.lookup(self.name, args)
        if cached is not None:
            self._cache_hits += 1
            self._record_urls(_extract_urls(cached))
            return cached

        actor_id = ACTOR_IDS[platform]
        run_input = _build_actor_input(
            platform, company, country, max_results,
            linkedin_company_url=linkedin_company_url,
            facebook_page_url=facebook_page_url,
            tiktok_handle=tiktok_handle,
        )
        used_canonical = _canonical_used(
            platform, linkedin_company_url, facebook_page_url, tiktok_handle,
        )
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

        # Hybrid post-filter (v5):
        # - Always-free-text-search means LinkedIn returns broad results
        #   covering the company AND name-collision noise.
        # - Filter accepts items matching by (a) canonical advertiser URL
        #   match (highest-confidence keep, even if name fuzzy-match fails)
        #   OR (b) name fuzzy-match OR (c) no advertiser field at all.
        # `url_boosted` counts items kept ONLY via canonical-URL match —
        # high values prove the canonical URL is doing real precision work.
        canonical_url = _canonical_for_filter(
            platform, linkedin_company_url, facebook_page_url, tiktok_handle,
        )
        filtered_items, dropped, url_boosted = _filter_by_advertiser(
            items, company, canonical_url=canonical_url,
        )
        text = _render_results(
            platform, company, country, filtered_items,
            dropped_count=dropped, used_canonical=used_canonical,
            url_boosted_count=url_boosted,
        )
        self.cache.store(self.name, args, text, ttl_hours=24 * 7)  # 7-day cache
        self._record_urls(_extract_urls(text))
        return text


# ---- per-actor input shapes ----

def _build_actor_input(
    platform: str, company: str, country: str, max_results: int,
    *,
    linkedin_company_url: str | None = None,
    facebook_page_url: str | None = None,
    tiktok_handle: str | None = None,
) -> dict[str, Any]:
    """Translate the generic tool args into the actor's expected input shape.

    Each Apify actor accepts a different schema; centralised here so swapping
    an actor (e.g. Meta switching scrapers) is a one-place change. Input shapes
    captured 2026-05-11 from each actor's published build inputSchema.

    Canonical-URL precedence (v4, 2026-05-12):
    - LinkedIn: linkedin_company_url → constructs the LinkedIn Ad Library URL
      anchored on the company slug (eliminates name collisions). Falls back
      to free-text `searchQuery` when no URL is provided.
    - Meta: facebook_page_url → passes the page URL as searchTerms (Meta's
      scraper resolves URLs to canonical pageIds internally). Falls back to
      free-text name search.
    - TikTok: tiktok_handle → builds a handle-anchored Ad Library URL.
      Falls back to brand-name URL.
    """
    if platform == "linkedin":
        # v5 (2026-05-12): ALWAYS free-text. Passing a canonical URL as
        # searchQuery cut Oracle's hit rate to zero — the actor matches the
        # URL as a literal string rather than resolving to the company.
        # Canonical URL is consumed downstream by _filter_by_advertiser as
        # a precision booster instead of a query constraint.
        return {
            "searchQuery": company,
            "maxAds": max_results,
            "dateRange": "past-year",
            "adFormat": "all",
        }
    if platform == "meta":
        # v5: same as LinkedIn — always free-text searchTerms with the brand
        # name; canonical Facebook page URL is a filter booster downstream.
        return {
            "searchTerms": [company],
            "country": country.upper(),
            "maxAds": max_results,
            "activeStatus": "active",
        }
    if platform == "tiktok":
        # `ivanvs/tiktok-ad-library-scraper` accepts library URLs only.
        # TikTok's URL has `adv_name=` which IS the canonical handle lookup
        # (not a free-text). When we have a handle, use it (precise); when we
        # don't, brand-name is the only option (post-filter cleans up).
        from urllib.parse import quote_plus
        region = country.upper()
        if tiktok_handle:
            handle_clean = tiktok_handle.lstrip("@")
            adv_name = quote_plus(handle_clean)
        else:
            adv_name = quote_plus(company)
        url = (
            f"https://library.tiktok.com/ads?region={region}"
            f"&adv_name={adv_name}&query_type=2&sort_type=last_shown_date,desc"
        )
        return {
            "urls": [{"url": url}],
            "maxRecords": max_results,
        }
    raise ValueError(f"Unsupported platform: {platform}")


def _canonical_used(
    platform: str,
    linkedin_company_url: str | None,
    facebook_page_url: str | None,
    tiktok_handle: str | None,
) -> bool:
    """Whether a canonical platform identifier was supplied for this call.
    Influences the rendered match_mode line so a reader can tell at a glance
    whether the result set went through canonical-URL boosting (high
    precision) or pure free-text + name-similarity filter."""
    if platform == "linkedin":
        return bool(linkedin_company_url)
    if platform == "meta":
        return bool(facebook_page_url)
    if platform == "tiktok":
        return bool(tiktok_handle)
    return False


def _canonical_for_filter(
    platform: str,
    linkedin_company_url: str | None,
    facebook_page_url: str | None,
    tiktok_handle: str | None,
) -> str | None:
    """The per-platform canonical URL that `_filter_by_advertiser` uses as
    the precision booster. None when no canonical was captured upstream —
    the filter then falls back to name-similarity only."""
    if platform == "linkedin":
        return linkedin_company_url
    if platform == "meta":
        return facebook_page_url
    if platform == "tiktok":
        # TikTok actor's URL already encoded the handle into adv_name; the
        # post-filter doesn't get a parallel item URL to match on, so we
        # rely on name similarity for TikTok.
        return None
    return None


# ---- advertiser-name post-filter (Layer 1 safety net) ----

def _tokenize_company_name(s: str | None) -> set[str]:
    """Lowercase, strip punctuation, drop legal-suffix tokens. Used by the
    fuzzy-match function below."""
    if not s:
        return set()
    out: set[str] = set()
    for raw in s.lower().replace("/", " ").replace("-", " ").split():
        token = raw.strip(".,;:!?\"'()[]{}")
        if not token:
            continue
        if token in _COMPANY_SUFFIX_TOKENS:
            continue
        out.add(token)
    return out


def _advertiser_similarity(advertiser: str | None, company: str) -> float:
    """Token-set Jaccard similarity between an ad's advertiser name and the
    target company. Returns 0.0 when either is empty (so 'unknown advertiser'
    items keep through the LATER `keep when no advertiser field` guard rather
    than getting dropped here)."""
    a = _tokenize_company_name(advertiser)
    b = _tokenize_company_name(company)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


_ADVERTISER_FIELDS: tuple[str, ...] = (
    "advertiser", "advertiserName", "advertiser_name",
    "pageName", "page_name", "page",
    "companyName", "company_name", "company",
    "adv_name", "advName", "brand",
)

# Fields where ad items expose the advertiser's CANONICAL profile URL (e.g.
# the LinkedIn /company/<slug>/ page that ran the ad). When this URL matches
# the canonical URL we captured in research_pass, we have a HIGH-confidence
# match — far stronger than name fuzzy-matching — and the item is kept even
# if token similarity is below threshold. Used as a precision booster only;
# never as a constraint (canonical URLs are not present on every actor).
_ADVERTISER_URL_FIELDS: tuple[str, ...] = (
    "advertiserUrl", "advertiser_url", "advertiserUrl_v2",
    "companyUrl", "company_url", "pageUrl", "page_url",
    "advertiser_page_url", "linkedinUrl", "linkedin_url",
)


def _extract_advertiser(item: dict[str, Any]) -> str | None:
    """Whichever advertiser-name field the actor surfaced — first non-empty
    string of the candidate list. None when the item lacks any."""
    for key in _ADVERTISER_FIELDS:
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            name = v.get("name") or v.get("title")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def _extract_advertiser_url(item: dict[str, Any]) -> str | None:
    """The advertiser's canonical profile URL on the platform, if present.
    Most LinkedIn ad-scraper actors expose this as `advertiserUrl` or
    `pageUrl` linking back to `linkedin.com/company/<slug>/`."""
    for key in _ADVERTISER_URL_FIELDS:
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            url = v.get("url") or v.get("href")
            if isinstance(url, str) and url.strip():
                return url.strip()
    return None


def _canonical_url_matches(item_url: str | None, canonical_url: str | None) -> bool:
    """True iff the item's advertiser URL points to the same canonical profile
    we captured upstream. Lenient on trailing slash, query strings, scheme."""
    if not item_url or not canonical_url:
        return False

    def _normalize(u: str) -> str:
        out = u.strip().lower().rstrip("/")
        # Drop scheme + leading www. so http vs https vs scheme-less matches.
        for prefix in ("https://", "http://", "//"):
            if out.startswith(prefix):
                out = out[len(prefix):]
        if out.startswith("www."):
            out = out[4:]
        # Strip query string.
        if "?" in out:
            out = out.split("?", 1)[0]
        return out

    a = _normalize(item_url)
    b = _normalize(canonical_url)
    # Either is a prefix of the other so /company/oracle matches /company/oracle/about.
    return a == b or a.startswith(b) or b.startswith(a)


def _filter_by_advertiser(
    items: list[dict[str, Any]],
    company: str,
    canonical_url: str | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """Drop items whose advertiser doesn't match the target company.

    Match rules (any one wins):
    1. Item's advertiser URL == canonical_url → keep (highest confidence)
    2. Advertiser-name token similarity ≥ threshold → keep (name match)
    3. No advertiser field at all → keep (partial signal beats zero)

    Otherwise drop.

    Returns (kept_items, dropped_count, url_boosted_count). `url_boosted` is
    a diagnostic — count of items kept that ONLY passed because of the
    canonical-URL match (would have failed the name filter alone). High value
    here is evidence the canonical URL is doing meaningful precision work.
    """
    kept: list[dict[str, Any]] = []
    dropped = 0
    url_boosted = 0
    for item in items:
        # 1. Canonical-URL match (highest-confidence bypass).
        if canonical_url:
            item_url = _extract_advertiser_url(item)
            if _canonical_url_matches(item_url, canonical_url):
                kept.append(item)
                # Track whether name filter would have failed without URL boost.
                adv = _extract_advertiser(item)
                if adv is not None:
                    if _advertiser_similarity(adv, company) < _ADVERTISER_MATCH_THRESHOLD:
                        url_boosted += 1
                continue

        # 2. Advertiser-name fuzzy match.
        advertiser = _extract_advertiser(item)
        if advertiser is None:
            # 3. No advertiser field — keep, can't judge.
            kept.append(item)
            continue
        sim = _advertiser_similarity(advertiser, company)
        if sim >= _ADVERTISER_MATCH_THRESHOLD:
            kept.append(item)
        else:
            dropped += 1
    return kept, dropped, url_boosted


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
    *,
    dropped_count: int = 0,
    used_canonical: bool = False,
    url_boosted_count: int = 0,
) -> str:
    """Render an Apify result set as plaintext tool output.

    Diagnostic lines (v5):
    - `match_mode`: "free-text+url-boost" when a canonical URL was captured
      upstream and used as a filter booster (BEST precision), "free-text+
      name-filter" otherwise (BEST coverage). v5 always does free-text
      search so we never see canonical-only anymore — the labels reflect
      whether the post-filter had URL ground-truth to work with.
    - `filtered_out`: items dropped by the post-filter (noise).
    - `url_boosted`: items kept ONLY via canonical-URL match (would have
      failed name-similarity alone). High = canonical URL doing real work.
    """
    match_mode = "free-text+url-boost" if used_canonical else "free-text+name-filter"
    if not items:
        return (
            f"Platform: {platform}\n"
            f"Company: {company}\n"
            f"Country: {country}\n"
            f"match_mode: {match_mode}\n"
            f"filtered_out: {dropped_count}\n"
            f"url_boosted: {url_boosted_count}\n"
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
        f"match_mode: {match_mode}\n"
        f"filtered_out: {dropped_count}\n"
        f"url_boosted: {url_boosted_count}\n"
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
