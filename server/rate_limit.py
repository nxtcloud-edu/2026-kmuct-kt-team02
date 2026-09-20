"""Small process-local rate limiter for expensive chat streams."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from uuid import UUID


Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int = 0


class SessionRateLimiter:
    """Allow a bounded number of chat turns per session over a rolling minute."""

    def __init__(self, *, limit_per_minute: int, clock: Clock = time.monotonic) -> None:
        if limit_per_minute <= 0:
            raise ValueError("limit_per_minute must be positive")
        self._limit = limit_per_minute
        self._clock = clock
        self._buckets: dict[UUID, deque[float]] = {}
        self._lock = Lock()

    def check(self, session_id: UUID) -> RateLimitResult:
        now = self._clock()
        cutoff = now - 60.0
        with self._lock:
            bucket = self._buckets.setdefault(session_id, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                retry = max(1, int(bucket[0] + 60.0 - now + 0.999))
                return RateLimitResult(allowed=False, retry_after_seconds=retry)
            bucket.append(now)
            return RateLimitResult(allowed=True)
