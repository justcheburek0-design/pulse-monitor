"""Monitor API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ConfigDict, field_validator, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.models.monitor import MonitorType, MonitorStatus
from src.services.monitor_service import MonitorService
from src.api.middleware.auth import get_current_user

router = APIRouter(prefix="/monitors", tags=["Monitors"])


class CreateMonitorRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    url: str = Field(..., min_length=1, max_length=2000)
    type: str = Field(default="https", pattern=r"^(https|tcp|icmp|dns|ssl|keyword)$")
    interval_seconds: int = Field(default=60, ge=10, le=86400)
    timeout_seconds: int = Field(default=10, ge=1, le=120)
    retries: int = Field(default=3, ge=0, le=10)
    description: Optional[str] = Field(None, max_length=2000)
    method: str = Field(default="GET", pattern=r"^(GET|POST|PUT|DELETE|HEAD|PATCH|OPTIONS)$")
    headers: Optional[str] = None
    expected_status_code: Optional[int] = Field(default=200, ge=100, le=599)
    expected_keyword: Optional[str] = None
    verify_ssl: bool = True
    follow_redirects: bool = True
    is_public: bool = False
    tags: Optional[str] = None


class UpdateMonitorRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    url: Optional[str] = Field(None, min_length=1, max_length=2000)
    type: Optional[str] = Field(None, pattern=r"^(https|tcp|icmp|dns|ssl|keyword)$")
    interval_seconds: Optional[int] = None
    timeout_seconds: Optional[int] = Field(None, ge=1, le=120)
    retries: Optional[int] = Field(None, ge=0, le=10)
    description: Optional[str] = None
    method: Optional[str] = Field(None, pattern=r"^(GET|POST|PUT|DELETE|HEAD|PATCH|OPTIONS)$")
    headers: Optional[str] = None
    expected_status_code: Optional[int] = Field(None, ge=100, le=599)
    expected_keyword: Optional[str] = None
    verify_ssl: Optional[bool] = None
    follow_redirects: Optional[bool] = None
    is_public: Optional[bool] = None
    is_active: Optional[bool] = None
    tags: Optional[str] = None


class MonitorResponse(BaseModel):
    id: str
    owner_id: str
    name: str
    url: str
    type: str
    interval_seconds: int
    timeout_seconds: int
    retries: int
    status: str
    is_active: bool
    is_public: bool
    uptime_percentage: float
    avg_response_time_ms: float
    description: Optional[str] = None
    method: str
    headers: Optional[str] = None
    expected_status_code: Optional[int] = None
    expected_keyword: Optional[str] = None
    verify_ssl: bool = True
    follow_redirects: bool = True
    tags: Optional[str] = None
    last_check_at: Optional[str] = None
    last_up_at: Optional[str] = None
    last_down_at: Optional[str] = None
    consecutive_failures: int
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "updated_at", "last_check_at", "last_up_at", "last_down_at")
    def serialize_dt(self, dt, _info):
        return dt.isoformat() if dt else None


class MonitorListResponse(BaseModel):
    items: List[MonitorResponse]
    total: int
    page: int
    page_size: int


class CheckResponse(BaseModel):
    id: str
    monitor_id: str
    is_up: bool
    status_code: Optional[int]
    response_time_ms: float
    error: Optional[str] = None
    created_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at")
    def serialize_dt(self, dt, _info):
        return dt.isoformat() if dt else None


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("", response_model=MonitorListResponse)
async def list_monitors(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = None,
):
    """List user monitors with pagination."""
    skip = (page - 1) * page_size
    monitors, total = await MonitorService.get_user_monitors(
        db, current_user.id, skip=skip, limit=page_size, status_filter=status_filter
    )
    return MonitorListResponse(
        items=[MonitorResponse.model_validate(m) for m in monitors],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=MonitorResponse, status_code=201)
async def create_monitor(
    request: CreateMonitorRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new monitor."""
    monitor = await MonitorService.create_monitor(
        db=db,
        owner_id=current_user.id,
        name=request.name,
        url=request.url,
        monitor_type=request.type,
        interval_seconds=request.interval_seconds,
        timeout_seconds=request.timeout_seconds,
        retries=request.retries,
        description=request.description,
        method=request.method,
        headers=request.headers,
        expected_status_code=request.expected_status_code,
        expected_keyword=request.expected_keyword,
        verify_ssl=request.verify_ssl,
        follow_redirects=request.follow_redirects,
    )
    return MonitorResponse.model_validate(monitor)


@router.get("/{monitor_id}", response_model=MonitorResponse)
async def get_monitor(
    monitor_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get monitor by ID."""
    monitor = await MonitorService.get_monitor(db, monitor_id, current_user.id)
    if not monitor:
        raise HTTPException(status_code=404, detail="Monitor not found")
    return MonitorResponse.model_validate(monitor)


@router.patch("/{monitor_id}", response_model=MonitorResponse)
async def update_monitor(
    monitor_id: str,
    request: UpdateMonitorRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update monitor."""
    monitor = await MonitorService.get_monitor(db, monitor_id, current_user.id)
    if not monitor:
        raise HTTPException(status_code=404, detail="Monitor not found")
    update_data = request.model_dump(exclude_unset=True)
    monitor = await MonitorService.update_monitor(db, monitor, **update_data)
    return MonitorResponse.model_validate(monitor)


@router.delete("/{monitor_id}", status_code=204)
async def delete_monitor(
    monitor_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Delete monitor."""
    monitor = await MonitorService.get_monitor(db, monitor_id, current_user.id)
    if not monitor:
        raise HTTPException(status_code=404, detail="Monitor not found")
    await MonitorService.delete_monitor(db, monitor)
    return None


@router.post("/{monitor_id}/pause", response_model=MonitorResponse)
async def pause_monitor(
    monitor_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Pause monitor."""
    monitor = await MonitorService.get_monitor(db, monitor_id, current_user.id)
    if not monitor:
        raise HTTPException(status_code=404, detail="Monitor not found")
    monitor = await MonitorService.pause_monitor(db, monitor)
    return MonitorResponse.model_validate(monitor)


@router.post("/{monitor_id}/resume", response_model=MonitorResponse)
async def resume_monitor(
    monitor_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Resume monitor."""
    monitor = await MonitorService.get_monitor(db, monitor_id, current_user.id)
    if not monitor:
        raise HTTPException(status_code=404, detail="Monitor not found")
    monitor = await MonitorService.resume_monitor(db, monitor)
    return MonitorResponse.model_validate(monitor)


@router.get("/{monitor_id}/checks", response_model=List[CheckResponse])
async def list_checks(
    monitor_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
):
    """List monitor check history."""
    monitor = await MonitorService.get_monitor(db, monitor_id, current_user.id)
    if not monitor:
        raise HTTPException(status_code=404, detail="Monitor not found")
    checks = await MonitorService.get_monitor_checks(db, monitor_id, limit=limit)
    return [CheckResponse.model_validate(c) for c in checks]
