"""A small in-memory sliding-window rate limiter.

    limiter = RateLimiter(limit=20, window_s=60)
    retry_after = limiter.hit("alice")   # None = allowed, else seconds to wait

It counts requests per key (the username for /chat) over the last `window_s` seconds.
State lives in this process only: with several backend workers or servers, each keeps
its own count, so put the limit in a shared store (Redis) or in front of the app
(Azure API Management, Front Door) before scaling out.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable


class RateLimiter:
    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> float | None:
        """Record a request for key. None if it's allowed, else seconds until one is."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(hits[0] + self.window_s - now, 0.001)
            hits.append(now)
            self._prune(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def _prune(self, now: float) -> None:
        """Drop keys with no recent requests, so memory doesn't grow with every user seen."""
        if len(self._hits) < 1000:
            return
        stale = [k for k, h in self._hits.items() if not h or h[-1] <= now - self.window_s]
        for k in stale:
            del self._hits[k]
