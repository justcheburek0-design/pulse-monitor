"""Request/response logging middleware."""

from __future__ import annotations

import time
import logging
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("pulse.access")


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logs all HTTP requests with timing."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.monotonic()
        client_ip = request.client.host if request.client else "unknown"
        logger.info(f"→ {request.method} {request.url.path} from {client_ip}")

        try:
            response = await call_next(request)
        except Exception as exc:
            duration = time.monotonic() - start
            logger.error(f"✗ {request.method} {request.url.path} — {type(exc).__name__} ({duration:.3f}s)")
            raise

        duration = time.monotonic() - start
        icon = "✓" if response.status_code < 400 else "✗"
        logger.info(f"{icon} {request.method} {request.url.path} — {response.status_code} ({duration:.3f}s)")
        response.headers["X-Response-Time"] = f"{duration:.3f}s"
        return response
