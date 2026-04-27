"""
Rate limiter in-memory para Gateway MCP.
Sliding window por key (IP, user, api_key).
"""
import time
from collections import defaultdict


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int = 1):
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after}s")


class InMemoryRateLimiter:
    def __init__(self):
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        """Check rate limit. Returns (allowed, remaining). Raises RateLimitExceeded."""
        now = time.time()
        window_start = now - window_seconds

        timestamps = [t for t in self._requests[key] if t > window_start]
        timestamps.append(now)
        self._requests[key] = timestamps

        if len(timestamps) > limit:
            self._requests[key] = timestamps[:-1]
            retry_after = max(1, int(timestamps[0] + window_seconds - now))
            raise RateLimitExceeded(retry_after=retry_after)

        return True, max(limit - len(timestamps), 0)

    def cleanup(self):
        """Purge expired entries."""
        now = time.time()
        stale = [k for k, ts in self._requests.items() if not ts or ts[-1] < now - 120]
        for k in stale:
            del self._requests[k]


def get_client_ip(request) -> str:
    """Extract client IP from request (Fly.io aware)."""
    return (
        request.headers.get("fly-client-ip")
        or (request.headers.get("x-forwarded-for", "").split(",")[0].strip())
        or (request.client.host if request.client else "unknown")
    )


# Singleton
rate_limiter = InMemoryRateLimiter()
