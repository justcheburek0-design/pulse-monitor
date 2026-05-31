"""Dashboard API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ConfigDict, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.api.middleware.auth import get_current_user
from src.models.dashboard import WidgetType
from src.services.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboards", tags=["Dashboards"])



class CreateDashboardRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=200, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = Field(None, max_length=2000)
    columns: int = Field(default=3, ge=1, le=6)
    theme: str = Field(default="dark", pattern=r"^(dark|light)$")
    team_id: Optional[str] = None


class UpdateDashboardRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    columns: Optional[int] = Field(None, ge=1, le=6)
    theme: Optional[str] = None
    is_public: Optional[bool] = None


class DashboardResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: Optional[str]
    columns: int
    theme: str
    is_public: bool
    public_slug: Optional[str]
    created_at: Optional[Any] = None

    model_config = ConfigDict(from_attributes=True)



    @field_serializer('created_at')
    def serialize_dt(self, dt, _info):
        return dt.isoformat() if dt else None

class WidgetRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    widget_type: str
    position: int = 0
    width: int = Field(default=1, ge=1, le=6)
    height: int = Field(default=1, ge=1, le=4)
    config: Optional[dict] = None
    monitor_ids: Optional[List[str]] = None


class WidgetResponse(BaseModel):
    id: str
    name: str
    widget_type: str
    position: int
    width: int
    height: int
    config: Optional[dict]
    monitor_ids: Optional[List[str]]

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=List[DashboardResponse])
async def list_dashboards(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List user dashboards."""
    dashboards = await DashboardService.get_user_dashboards(db, current_user.id)
    return [DashboardResponse.model_validate(d) for d in dashboards]


@router.post("", response_model=DashboardResponse, status_code=status.HTTP_201_CREATED)
async def create_dashboard(
    request: CreateDashboardRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new dashboard."""
    dashboard = await DashboardService.create_dashboard(
        db=db,
        user_id=current_user.id,
        name=request.name,
        slug=request.slug,
        description=request.description,
        columns=request.columns,
        theme=request.theme,
        team_id=request.team_id,
    )
    return DashboardResponse.model_validate(dashboard)


@router.get("/{dashboard_id}")
async def get_dashboard(
    dashboard_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get dashboard with widget data."""
    data = await DashboardService.get_dashboard_with_data(db, dashboard_id, current_user.id)
    if not data:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return data


@router.patch("/{dashboard_id}", response_model=DashboardResponse)
async def update_dashboard(
    dashboard_id: str,
    request: UpdateDashboardRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update a dashboard."""
    dashboard = await DashboardService.get_dashboard(db, dashboard_id, current_user.id)
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    updated = await DashboardService.update_dashboard(dashboard, **request.model_dump(exclude_unset=True))
    return DashboardResponse.model_validate(updated)


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(
    dashboard_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Delete a dashboard."""
    dashboard = await DashboardService.get_dashboard(db, dashboard_id, current_user.id)
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    await DashboardService.delete_dashboard(db, dashboard)


# Widgets

@router.post("/{dashboard_id}/widgets", response_model=WidgetResponse, status_code=status.HTTP_201_CREATED)
async def add_widget(
    dashboard_id: str,
    request: WidgetRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Add a widget to a dashboard."""
    dashboard = await DashboardService.get_dashboard(db, dashboard_id, current_user.id)
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    widget = await DashboardService.add_widget(
        db=db,
        dashboard_id=dashboard_id,
        name=request.name,
        widget_type=request.widget_type,
        position=request.position,
        width=request.width,
        height=request.height,
        config=request.config,
        monitor_ids=request.monitor_ids,
    )
    return WidgetResponse.model_validate(widget)


@router.patch("/{dashboard_id}/widgets/{widget_id}", response_model=WidgetResponse)
async def update_widget(
    dashboard_id: str,
    widget_id: str,
    request: WidgetRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update a widget."""
    from sqlalchemy import select
    from src.models.dashboard import DashboardWidget
    result = await db.execute(select(DashboardWidget).where(DashboardWidget.id == widget_id))
    widget = result.scalar_one_or_none()
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")
    updated = await DashboardService.update_widget(widget, **request.model_dump(exclude_unset=True))
    return WidgetResponse.model_validate(updated)


@router.delete("/{dashboard_id}/widgets/{widget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_widget(
    dashboard_id: str,
    widget_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Delete a widget."""
    from sqlalchemy import select
    from src.models.dashboard import DashboardWidget
    result = await db.execute(select(DashboardWidget).where(DashboardWidget.id == widget_id))
    widget = result.scalar_one_or_none()
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")
    await DashboardService.delete_widget(db, widget)
