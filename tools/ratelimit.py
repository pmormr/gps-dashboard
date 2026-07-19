"""Thread-shared request pacer for the fetch tools.

A global minimum interval between request *starts*, shared across all worker
threads so the effective request rate is independent of the worker count.
``tools/precache.py`` (USGS tiles) and ``tools/fetch_wikipedia.py`` (Wikimedia)
both need it to stay polite under concurrency; concurrency then only hides
latency, it never multiplies the request rate.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """Global pacer: caps request *starts* to at most ``rate`` per second.

    Shared across worker threads, so the request rate is independent of worker
    count. A rate of 0 (or negative) disables limiting. The next slot is reserved
    under a lock but slept for *outside* it, so threads pipeline their waits
    (accurate spacing without serializing the workers on the sleeper).
    """

    def __init__(self, rate: float) -> None:
        """Initialize the limiter.

        Args:
            rate: Maximum request starts per second across all threads. 0 or
                negative disables throttling.
        """
        self._min_interval = 1.0 / rate if rate > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        """Block until the caller is allowed to issue its next request."""
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next - now
            self._next = max(now, self._next) + self._min_interval
        if wait > 0:
            time.sleep(wait)
