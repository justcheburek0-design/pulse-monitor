"""Health check and public status endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.config.settings import get_settings
from src.models.monitor import Monitor, MonitorStatus

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str
    uptime_seconds: Optional[int] = None


class PublicStatusResponse(BaseModel):
    monitor_id: str
    name: str
    status: str
    uptime_percentage: float
    avg_response_time_ms: float
    last_check_at: Optional[str]


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Public health check endpoint."""
    settings = get_settings()
    return HealthResponse(
        status="healthy",
        version=settings.app_version,
        timestamp=datetime.utcnow().isoformat(),
    )


@router.get("/status/{monitor_id}", response_model=PublicStatusResponse)
async def public_status(
    monitor_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Public status page for a monitor (no auth required)."""
    monitor = await db.get(Monitor, monitor_id)
    if not monitor or not monitor.is_public:
        raise HTTPException(status_code=404, detail="Monitor not found or not public")
    
    return PublicStatusResponse(
        monitor_id=monitor.id,
        name=monitor.name,
        status=monitor.status,
        uptime_percentage=monitor.uptime_percentage,
        avg_response_time_ms=monitor.avg_response_time_ms,
        last_check_at=monitor.last_check_at.isoformat() if monitor.last_check_at else None,
    )


@router.get("/status/{monitor_id}/badge")
async def status_badge(
    monitor_id: str,
    db: AsyncSession = Depends(get_db),
):
    """SVG status badge for embedding."""
    monitor = await db.get(Monitor, monitor_id)
    if not monitor or not monitor.is_public:
        raise HTTPException(status_code=404, detail="Not found")
    
    from fastapi.responses import Response
    color = "brightgreen" if monitor.status == "up" else "red" if monitor.status == "down" else "yellow"
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="120" height="20">
        <rect width="70" height="20" fill="#555"/>
        <rect x="70" width="50" height="20" fill="{color}"/>
        <text x="35" y="14" fill="#fff" font-size="11" text-anchor="middle">pulse</text>
        <text x="95" y="14" fill="#fff" font-size="11" text-anchor="middle">{monitor.status}</text>
    </svg>'''
    return Response(content=svg, media_type="image/svg+xml")
