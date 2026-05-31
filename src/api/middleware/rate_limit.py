"""Token bucket rate limiter per IP address."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, Tuple

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimitEntry:
    """Tracks rate limit state for a single client."""

    def __init__(self, max_requests: int, period_seconds: int):
        self.max_requests = max_requests
        self.period_seconds = period_seconds
        self.tokens = float(max_requests)
        self.last_refill = time.monotonic()

    def consume(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(
            self.max_requests,
            self.tokens + elapsed * (self.max_requests / self.period_seconds),
        )
        self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    @property
    def retry_after(self) -> float:
        if self.tokens >= 1:
            return 0
        return (1 - self.tokens) * (self.period_seconds / self.max_requests)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 100, period_seconds: int = 60, exclude_paths=None):
        super().__init__(app)
        self.max_requests = max_requests
        self.period_seconds = period_seconds
        self.exclude_paths = exclude_paths or ["/health", "/docs", "/openapi.json"]
        self._storage: Dict[str, RateLimitEntry] = defaultdict(
            lambda: RateLimitEntry(max_requests, period_seconds)
        )

    async def dispatch(self, request: Request, call_next):
        if any(request.url.path.startswith(p) for p in self.exclude_paths):
            return await call_next(request)
        client_ip = request.client.host if request.client else "unknown"
        entry = self._storage[client_ip]
        if not entry.consume():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(int(entry.retry_after) + 1)},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(int(entry.tokens))
        return response
