"""Per-service rate limiters (Fix Appendix #14).

A single DEFAULT_CONCURRENCY value cannot safely govern Notion (3 rps),
Anthropic (50 rpm tier-1), Tavily (10 rps), JobSpy (per-board), and Apify
(actor-specific). Each external service gets its own RateLimiter; callers
acquire the limiter as a context manager around the actual network call.

Account-level concurrency (DEFAULT_CONCURRENCY) is unchanged — these limiters
sit underneath, throttling per-service traffic regardless of how many account
workers are active.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """Token-bucket-style limiter that blocks until the next slot is available.

    Construct with EITHER `rate_per_second` or `rate_per_minute` (not both).
    The limiter is thread-safe and reentrant-safe per call site (one acquisition
    per `with` block).
    """

    def __init__(
        self,
        rate_per_second: float | None = None,
        rate_per_minute: float | None = None,
    ):
        if rate_per_second is None and rate_per_minute is None:
            raise ValueError("Specify rate_per_second or rate_per_minute")
        if rate_per_second is None:
            rate_per_second = float(rate_per_minute) / 60.0
        if rate_per_second <= 0:
            raise ValueError("Rate must be positive")
        self._min_interval = 1.0 / float(rate_per_second)
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = max(now, self._next_allowed) + self._min_interval

    def __enter__(self) -> "RateLimiter":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        return None


# Service limiters. Tune as new tier limits land — the constants are the
# only thing that should change with provider plan upgrades.
NOTION_LIMITER = RateLimiter(rate_per_second=2.5)      # Notion's documented 3 rps, with headroom
TAVILY_LIMITER = RateLimiter(rate_per_second=8)         # Tavily standard tier
ANTHROPIC_LIMITER = RateLimiter(rate_per_minute=50)     # tier-1 default; bump on upgrade
OPENAI_LIMITER = RateLimiter(rate_per_minute=500)       # tier-1 chat completions
JOBSPY_LIMITER = RateLimiter(rate_per_second=1)         # generous to avoid board bans
APIFY_LIMITER = RateLimiter(rate_per_second=2)          # safe default for actor runs
BRAVE_LIMITER = RateLimiter(rate_per_second=20)         # Brave free tier 20 rps
JINA_LIMITER = RateLimiter(rate_per_second=5)           # Jina Reader free tier
