
"""Pulse — Monitoring & Alerting Platform.

Production-ready SaaS platform for HTTP/TCP/ICMP monitoring
with alerts, dashboards, incidents, and team management.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.config.settings import get_settings
from src.config.database import init_db, close_db
from src.api.middleware.cors import setup_cors
from src.api.middleware.rate_limit import RateLimitMiddleware
from src.api.middleware.logging_middleware import LoggingMiddleware
from src.api.middleware.error_handler import setup_error_handlers
from src.api.routes.auth import router as auth_router
from src.api.routes.monitors import router as monitors_router
from src.api.routes.dashboards import router as dashboards_router
from src.api.routes.teams import router as teams_router
from src.api.routes.incidents import router as incidents_router
from src.api.routes.alerts import router as alerts_router
from src.api.routes.health import router as health_router
from src.workers.scheduler import MonitorScheduler

settings = get_settings()
logger = logging.getLogger("pulse")

# Global scheduler instance
scheduler = MonitorScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    await init_db()
    await scheduler.start()
    logger.info("Application ready")
    yield
    await scheduler.stop()
    await close_db()
    logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(
        title=settings.app_name,
        description=settings.app_description,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    # Middleware (order matters — first added = outermost)
    setup_cors(app)
    app.add_middleware(RateLimitMiddleware, max_requests=settings.rate_limit_requests, period_seconds=settings.rate_limit_period_seconds)
    app.add_middleware(LoggingMiddleware)

    # Error handlers
    setup_error_handlers(app)

    # Static files
    from pathlib import Path
    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Routes
    app.include_router(health_router, prefix="")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(monitors_router, prefix="/api/v1")
    app.include_router(dashboards_router, prefix="/api/v1")
    app.include_router(teams_router, prefix="/api/v1")
    app.include_router(incidents_router, prefix="/api/v1")
    app.include_router(alerts_router, prefix="/api/v1")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        workers=settings.workers,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
