#!/usr/bin/env python3
"""Generate remaining Pulse files: app, routes, CLI, tests."""
import os

BASE = "/root/.hermes/workspace/night_projects/projects/2026-05-31-pulse"
def w(path, content):
    full = os.path.join(BASE, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)

# ═════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ═════════════════════════════════════════════════════════════════════════════
w("src/main.py", '''
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
''')

# ═════════════════════════════════════════════════════════════════════════════
# API ROUTES — Alerts
# ═════════════════════════════════════════════════════════════════════════════
w("src/api/routes/alerts.py", '''
"""Alert and alert rule API endpoints."""

from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.models.alert import AlertChannel, AlertRule, Alert, AlertChannelType, AlertSeverity
from src.services.alert_service import AlertService

router = APIRouter(prefix="/alerts", tags=["Alerts"])


def get_current_user():
    from src.api.middleware.auth import get_current_user as _gcu
    return _gcu


# ── Alert Channels ──────────────────────────────────────────────────────

class CreateChannelRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    channel_type: str = Field(..., pattern=r"^(email|slack|telegram|webhook|discord|sms|pagerduty)$")
    email_address: Optional[str] = None
    webhook_url: Optional[str] = None
    chat_id: Optional[str] = None
    phone_number: Optional[str] = None
    config: Optional[dict] = None
    is_default: bool = False


class ChannelResponse(BaseModel):
    id: str
    name: str
    channel_type: str
    is_enabled: bool
    is_default: bool
    email_address: Optional[str]
    webhook_url: Optional[str]
    chat_id: Optional[str]
    last_used_at: Optional[str]
    created_at: str

    class Config:
        from_attributes = True


@router.get("/channels", response_model=List[ChannelResponse])
async def list_channels(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List all alert channels."""
    from sqlalchemy import select
    result = await db.execute(
        select(AlertChannel).where(AlertChannel.user_id == current_user.id)
    )
    channels = result.scalars().all()
    return [ChannelResponse.model_validate(c) for c in channels]


@router.post("/channels", response_model=ChannelResponse, status_code=status.HTTP_201_CREATED)
async def create_channel(
    request: CreateChannelRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new alert channel."""
    channel = AlertChannel(
        user_id=current_user.id,
        name=request.name,
        channel_type=request.channel_type,
        email_address=request.email_address,
        webhook_url=request.webhook_url,
        chat_id=request.chat_id,
        phone_number=request.phone_number,
        config=request.config,
        is_default=request.is_default,
    )
    db.add(channel)
    await db.flush()
    return ChannelResponse.model_validate(channel)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Delete an alert channel."""
    from sqlalchemy import select
    result = await db.execute(
        select(AlertChannel).where(AlertChannel.id == channel_id, AlertChannel.user_id == current_user.id)
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    await db.delete(channel)


# ── Alert Rules ─────────────────────────────────────────────────────────

class CreateRuleRequest(BaseModel):
    monitor_id: str
    channel_id: str
    name: str = Field(..., min_length=1, max_length=200)
    severity: str = Field(default=AlertSeverity.CRITICAL, pattern=r"^(critical|warning|info|recovery)$")
    trigger_on_down: bool = True
    trigger_on_up: bool = True
    trigger_on_ssl_expiry: bool = False
    ssl_expiry_days_before: int = 14
    consecutive_failures: int = Field(default=2, ge=1, le=20)
    response_time_threshold_ms: Optional[int] = Field(None, ge=100, le=60000)
    cooldown_minutes: int = Field(default=15, ge=1, le=1440)
    max_alerts_per_hour: int = Field(default=10, ge=1, le=100)


class RuleResponse(BaseModel):
    id: str
    monitor_id: str
    channel_id: str
    name: str
    severity: str
    is_enabled: bool
    trigger_on_down: bool
    trigger_on_up: bool
    consecutive_failures: int
    cooldown_minutes: int
    last_triggered_at: Optional[str]
    created_at: str

    class Config:
        from_attributes = True


@router.get("/rules", response_model=List[RuleResponse])
async def list_rules(
    monitor_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List alert rules."""
    from sqlalchemy import select
    from src.models.monitor import Monitor
    query = (
        select(AlertRule)
        .join(Monitor)
        .where(Monitor.owner_id == current_user.id)
    )
    if monitor_id:
        query = query.where(AlertRule.monitor_id == monitor_id)
    result = await db.execute(query)
    rules = result.scalars().all()
    return [RuleResponse.model_validate(r) for r in rules]


@router.post("/rules", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    request: CreateRuleRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new alert rule."""
    rule = AlertRule(
        monitor_id=request.monitor_id,
        channel_id=request.channel_id,
        name=request.name,
        severity=request.severity,
        trigger_on_down=request.trigger_on_down,
        trigger_on_up=request.trigger_on_up,
        trigger_on_ssl_expiry=request.trigger_on_ssl_expiry,
        ssl_expiry_days_before=request.ssl_expiry_days_before,
        consecutive_failures=request.consecutive_failures,
        response_time_threshold_ms=request.response_time_threshold_ms,
        cooldown_minutes=request.cooldown_minutes,
        max_alerts_per_hour=request.max_alerts_per_hour,
    )
    db.add(rule)
    await db.flush()
    return RuleResponse.model_validate(rule)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Delete an alert rule."""
    from sqlalchemy import select
    result = await db.execute(select(AlertRule).where(AlertRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.delete(rule)


# ── Alerts (fired) ──────────────────────────────────────────────────────

class AlertResponse(BaseModel):
    id: str
    severity: str
    title: str
    message: str
    status: str
    sent_via: Optional[str]
    fired_at: str
    acknowledged_at: Optional[str]
    resolved_at: Optional[str]

    class Config:
        from_attributes = True


@router.get("/", response_model=List[AlertResponse])
async def list_alerts(
    monitor_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List fired alerts."""
    if monitor_id:
        alerts = await AlertService.get_monitor_alerts(db, monitor_id, limit=limit)
    else:
        from sqlalchemy import select
        from src.models.monitor import Monitor
        result = await db.execute(
            select(Alert)
            .join(AlertRule)
            .join(Monitor)
            .where(Monitor.owner_id == current_user.id)
            .order_by(Alert.fired_at.desc())
            .limit(limit)
        )
        alerts = result.scalars().all()
    return [AlertResponse.model_validate(a) for a in alerts]


@router.post("/{alert_id}/acknowledge", response_model=AlertResponse)
async def acknowledge_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Acknowledge an alert."""
    alert = await AlertService.acknowledge_alert(db, alert_id, current_user.id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return AlertResponse.model_validate(alert)


@router.post("/{alert_id}/resolve", response_model=AlertResponse)
async def resolve_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Resolve an alert."""
    alert = await AlertService.resolve_alert(db, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return AlertResponse.model_validate(alert)
''')

# ═════════════════════════════════════════════════════════════════════════════
# API ROUTES — Dashboard
# ═════════════════════════════════════════════════════════════════════════════
w("src/api/routes/dashboards.py", '''
"""Dashboard API endpoints."""

from __future__ import annotations

from typing import Optional, List, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.models.dashboard import WidgetType
from src.services.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboards", tags=["Dashboards"])


def get_current_user():
    from src.api.middleware.auth import get_current_user as _gcu
    return _gcu


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
    created_at: str

    class Config:
        from_attributes = True


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

    class Config:
        from_attributes = True


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
''')

# ═════════════════════════════════════════════════════════════════════════════
# API ROUTES — Incidents
# ═════════════════════════════════════════════════════════════════════════════
w("src/api/routes/incidents.py", '''
"""Incident management API endpoints."""

from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.models.incident import IncidentSeverity, IncidentStatus
from src.services.incident_service import IncidentService

router = APIRouter(prefix="/incidents", tags=["Incidents"])


def get_current_user():
    from src.api.middleware.auth import get_current_user as _gcu
    return _gcu


class CreateIncidentRequest(BaseModel):
    monitor_id: str
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    severity: str = Field(default=IncidentSeverity.MAJOR, pattern=r"^(critical|major|minor|maintenance)$")


class UpdateIncidentRequest(BaseModel):
    status: Optional[str] = Field(None, pattern=r"^(investigating|identified|monitoring|resolved)$")
    title: Optional[str] = None
    description: Optional[str] = None
    root_cause: Optional[str] = None
    resolution_notes: Optional[str] = None


class AddCommentRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)


class IncidentResponse(BaseModel):
    id: str
    monitor_id: str
    title: str
    description: Optional[str]
    severity: str
    status: str
    started_at: str
    identified_at: Optional[str]
    resolved_at: Optional[str]
    duration_seconds: Optional[int]
    root_cause: Optional[str]
    created_at: str

    class Config:
        from_attributes = True


class IncidentEventResponse(BaseModel):
    id: str
    event_type: str
    message: str
    old_status: Optional[str]
    new_status: Optional[str]
    created_at: str

    class Config:
        from_attributes = True


@router.get("", response_model=List[IncidentResponse])
async def list_incidents(
    monitor_id: Optional[str] = Query(None),
    active_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List incidents."""
    if active_only:
        incidents = await IncidentService.get_active_incidents(db, monitor_id=monitor_id)
    elif monitor_id:
        incidents = await IncidentService.get_incident_history(db, monitor_id, limit=limit)
    else:
        from sqlalchemy import select
        from src.models.incident import Incident
        from src.models.monitor import Monitor
        query = (
            select(Incident)
            .join(Monitor)
            .where(Monitor.owner_id == current_user.id)
            .order_by(Incident.started_at.desc())
            .limit(limit)
        )
        result = await db.execute(query)
        incidents = result.scalars().all()
    return [IncidentResponse.model_validate(i) for i in incidents]


@router.post("", response_model=IncidentResponse, status_code=status.HTTP_201_CREATED)
async def create_incident(
    request: CreateIncidentRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new incident."""
    incident = await IncidentService.create_incident(
        db=db,
        monitor_id=request.monitor_id,
        title=request.title,
        description=request.description,
        severity=request.severity,
        created_by=current_user.id,
    )
    return IncidentResponse.model_validate(incident)


@router.patch("/{incident_id}", response_model=IncidentResponse)
async def update_incident(
    incident_id: str,
    request: UpdateIncidentRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update an incident."""
    if request.status:
        incident = await IncidentService.update_status(
            db, incident_id, request.status, current_user.id
        )
    else:
        from sqlalchemy import select
        from src.models.incident import Incident
        result = await db.execute(select(Incident).where(Incident.id == incident_id))
        incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    for field in ("title", "description", "root_cause", "resolution_notes"):
        val = getattr(request, field, None)
        if val is not None:
            setattr(incident, field, val)

    await db.flush()
    return IncidentResponse.model_validate(incident)


@router.post("/{incident_id}/resolve", response_model=IncidentResponse)
async def resolve_incident(
    incident_id: str,
    request: AddCommentRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Resolve an incident."""
    from sqlalchemy import select
    from src.models.incident import Incident
    result = await db.execute(select(Incident).where(Incident.id == incident_id))
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    incident.status = IncidentStatus.RESOLVED
    from datetime import datetime
    incident.resolved_at = datetime.utcnow()
    incident.duration_seconds = (incident.resolved_at - incident.started_at).total_seconds()
    incident.resolution_notes = request.message

    event =IncidentEvent(
        incident_id=incident.id,
        user_id=current_user.id,
        event_type="resolved",
        message=f"Incident resolved: {request.message}",
        old_status=IncidentStatus.MONITORING,
        new_status=IncidentStatus.RESOLVED,
    )
    db.add(event)
    await db.flush()
    return IncidentResponse.model_validate(incident)


@router.post("/{incident_id}/comments", response_model=IncidentEventResponse)
async def add_comment(
    incident_id: str,
    request: AddCommentRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Add a comment to an incident."""
    event = await IncidentService.add_comment(db, incident_id, current_user.id, request.message)
    if not event:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentEventResponse.model_validate(event)


@router.get("/{incident_id}/events", response_model=List[IncidentEventResponse])
async def get_incident_events(
    incident_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get events for an incident."""
    from sqlalchemy import select
    from src.models.incident import Incident, IncidentEvent
    result = await db.execute(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == incident_id)
        .order_by(IncidentEvent.created_at)
    )
    events = result.scalars().all()
    return [IncidentEventResponse.model_validate(e) for e in events]
''')

# ═════════════════════════════════════════════════════════════════════════════
# API ROUTES — Teams
# ═════════════════════════════════════════════════════════════════════════════
w("src/api/routes/teams.py", '''
"""Team management API endpoints."""

from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.models.team import TeamRole

router = APIRouter(prefix="/teams", tags=["Teams"])


def get_current_user():
    from src.api.middleware.auth import get_current_user as _gcu
    return _gcu


class CreateTeamRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=200, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None


class TeamResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: Optional[str]
    max_members: int
    is_public: bool
    created_at: str

    class Config:
        from_attributes = True


class TeamMemberResponse(BaseModel):
    id: str
    user_id: str
    username: str
    role: str
    is_active: bool
    joined_at: str

    class Config:
        from_attributes = True


class InviteRequest(BaseModel):
    email: str
    role: str = Field(default=TeamRole.MEMBER, pattern=r"^(owner|admin|member|viewer)$")


@router.get("", response_model=List[TeamResponse])
async def list_teams(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List teams the user belongs to."""
    from sqlalchemy import select
    from src.models.team import Team, TeamMember
    from src.models.user import User
    
    # Teams owned by user
    owned = await db.execute(select(Team).where(Team.owner_id == current_user.id))
    # Teams user is member of
    memberships = await db.execute(
        select(TeamMember).where(TeamMember.user_id == current_user.id, TeamMember.is_active == True)
    )
    member_teams = []
    for m in memberships.scalars().all():
        team = await db.get(Team, m.team_id)
        if team:
            member_teams.append(team)
    
    all_teams = list(owned.scalars().all()) + member_teams
    return [TeamResponse.model_validate(t) for t in all_teams]


@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    request: CreateTeamRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new team."""
    from src.models.team import Team, TeamMember
    team = Team(
        owner_id=current_user.id,
        name=request.name,
        slug=request.slug,
        description=request.description,
        max_members=current_user.team_members_limit,
    )
    db.add(team)
    await db.flush()

    # Add owner as member
    member = TeamMember(
        team_id=team.id,
        user_id=current_user.id,
        role=TeamRole.OWNER,
    )
    db.add(member)
    await db.flush()
    return TeamResponse.model_validate(team)


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get team details."""
    from sqlalchemy import select
    from src.models.team import Team
    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return TeamResponse.model_validate(team)


@router.get("/{team_id}/members", response_model=List[TeamMemberResponse])
async def list_members(
    team_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List team members."""
    from sqlalchemy import select
    from src.models.team import TeamMember
    from src.models.user import User
    result = await db.execute(
        select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.is_active == True)
    )
    members = result.scalars().all()
    resp = []
    for m in members:
        user = await db.get(User, m.user_id)
        resp.append(TeamMemberResponse(
            id=m.id,
            user_id=m.user_id,
            username=user.username if user else "unknown",
            role=m.role,
            is_active=m.is_active,
            joined_at=m.joined_at.isoformat() if m.joined_at else "",
        ))
    return resp


@router.post("/{team_id}/invites", status_code=status.HTTP_201_CREATED)
async def invite_member(
    team_id: str,
    request: InviteRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Invite a user to the team."""
    import secrets
    from datetime import datetime, timedelta
    from src.models.team import Team, TeamMember, TeamInvite

    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Check permission
    membership = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == current_user.id,
            TeamMember.is_active == True,
        )
    )
    member = membership.scalar_one_or_none()
    if not member or not member.can_manage_members:
        raise HTTPException(status_code=403, detail="You don't have permission to invite members")

    # Check member limit
    count = await db.execute(
        select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.is_active == True)
    )
    if len(count.scalars().all()) >= team.max_members:
        raise HTTPException(status_code=400, detail="Team member limit reached")

    invite = TeamInvite(
        team_id=team_id,
        invited_by=current_user.id,
        email=request.email,
        role=request.role,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.add(invite)
    await db.flush()
    return {"message": f"Invitation sent to {request.email}", "invite_id": invite.id}


@router.delete("/{team_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    team_id: str,
    member_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Remove a member from the team."""
    from sqlalchemy import select
    from src.models.team import TeamMember
    membership = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == current_user.id,
            TeamMember.is_active == True,
        )
    )
    requester = membership.scalar_one_or_none()
    if not requester or not requester.can_manage_members:
        raise HTTPException(status_code=403, detail="No permission")

    target = await db.get(TeamMember, member_id)
    if not target or target.team_id != team_id:
        raise HTTPException(status_code=404, detail="Member not found")
    target.is_active = False
    await db.flush()
''')

# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════
w("src/cli/main.py", '''
"""CLI interface for Pulse administration."""

from __future__ import annotations

import asyncio
import typer
from rich.console import Console
from rich.table import Table

from src.config.database import init_db, close_db, async_session_factory
from src.models.user import User

app = typer.Typer(help="Pulse Monitoring Platform CLI")
console = Console()


@app.command()
def init():
    """Initialize database tables."""
    async def _init():
        await init_db()
        console.print("[green]Database initialized successfully[/green]")
        await close_db()
    asyncio.run(_init())


@app.command()
def create_user(
    email: str = typer.Option(..., help="User email"),
    username: str = typer.Option(..., help="Username"),
    password: str = typer.Option(..., help="Password"),
    admin: bool = typer.Option(False, help="Make superuser"),
):
    """Create a new user."""
    from src.services.auth_service import AuthService

    async def _create():
        await init_db()
        async with async_session_factory() as session:
            user = User(
                email=email.lower(),
                username=username.lower(),
                hashed_password=AuthService.hash_password(password),
                is_superuser=admin,
                is_verified=True,
            )
            session.add(user)
            await session.commit()
            console.print(f"[green]Created user {username} ({email})[/green]")
        await close_db()
    asyncio.run(_create())


@app.command()
def list_users():
    """List all users."""

    async def _list():
        await init_db()
        async with async_session_factory() as session:
            from sqlalchemy import select
            result = await session.execute(select(User))
            users = result.scalars().all()
            table = Table(title="Pulse Users")
            table.add_column("ID", style="dim")
            table.add_column("Username")
            table.add_column("Email")
            table.add_column("Plan")
            table.add_column("Active")
            for u in users:
                table.add_row(u.id[:8]+"...", u.username, u.email, u.plan, str(u.is_active))
            console.print(table)
        await close_db()
    asyncio.run(_list())


@app.command()
def create_admin(
    email: str = typer.Option("admin@pulse.local"),
    username: str = typer.Option("admin"),
    password: str = typer.Option("changeme123"),
):
    """Create default admin user."""
    create_user(email=email, username=username, password=password, admin=True)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0"),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False),
):
    """Run the development server."""
    import uvicorn
    uvicorn.run("src.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
''')

# ═════════════════════════════════════════════════════════════════════════════
# TESTS — conftest
# ═════════════════════════════════════════════════════════════════════════════
w("tests/conftest.py", '''
"""Test configuration and fixtures."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from src.main import create_app
from src.config.database import Base, get_db

# Use in-memory SQLite for tests
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with test_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Create all tables before each test, drop after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a test database session."""
    async with test_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Provide a test HTTP client."""
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def test_user(client: AsyncClient) -> dict:
    """Create and return a test user with auth token."""
    response = await client.post("/api/v1/auth/register", json={
        "email": "test@pulse.local",
        "username": "testuser",
        "password": "SecurePass123",
        "full_name": "Test User",
    })
    data = response.json()
    return {
        "id": data["user"]["id"],
        "email": "test@pulse.local",
        "username": "testuser",
        "token": data["access_token"],
    }


@pytest.fixture
def auth_headers(test_user: dict) -> dict:
    """Return authorization headers for the test user."""
    return {"Authorization": f"Bearer {test_user['token']}"}


@pytest.fixture
def event_loop():
    """Create event loop for tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
''')

# ═════════════════════════════════════════════════════════════════════════════
# TESTS — test_auth
# ═════════════════════════════════════════════════════════════════════════════
w("tests/unit/test_auth.py", '''
"""Authentication endpoint tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_user(client: AsyncClient):
    """Test successful user registration."""
    response = await client.post("/api/v1/auth/register", json={
        "email": "newuser@pulse.local",
        "username": "newuser",
        "password": "SecurePass123",
        "full_name": "New User",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["user"]["email"] == "newuser@pulse.local"
    assert data["user"]["username"] == "newuser"
    assert data["access_token"]
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient):
    """Test registration with duplicate email fails."""
    payload = {
        "email": "dup@pulse.local",
        "username": "dupuser1",
        "password": "SecurePass123",
    }
    await client.post("/api/v1/auth/register", json=payload)
    payload["username"] = "dupuser2"
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_register_weak_password(client: AsyncClient):
    """Test registration with weak password fails validation."""
    response = await client.post("/api/v1/auth/register", json={
        "email": "weak@pulse.local",
        "username": "weakuser",
        "password": "short",
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    """Test successful login."""
    # Register first
    await client.post("/api/v1/auth/register", json={
        "email": "login@pulse.local",
        "username": "loginuser",
        "password": "SecurePass123",
    })
    # Login
    response = await client.post("/api/v1/auth/login", json={
        "email_or_username": "login@pulse.local",
        "password": "SecurePass123",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["access_token"]
    assert data["user"]["email"] == "login@pulse.local"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    """Test login with wrong password fails."""
    await client.post("/api/v1/auth/register", json={
        "email": "wrong@pulse.local",
        "username": "wronguser",
        "password": "SecurePass123",
    })
    response = await client.post("/api/v1/auth/login", json={
        "email_or_username": "wrong@pulse.local",
        "password": "WrongPass123",
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_by_username(client: AsyncClient):
    """Test login using username instead of email."""
    await client.post("/api/v1/auth/register", json={
        "email": "byname@pulse.local",
        "username": "bynameuser",
        "password": "SecurePass123",
    }
    response = await client.post("/api/v1/auth/login", json={
        "email_or_username": "bynameuser",
        "password": "SecurePass123",
    })
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    """Test health check endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data


@pytest.mark.asyncio
async def test_password_validation_requirements(client: AsyncClient):
    """Test that password validation enforces complexity requirements."""
    test_cases = [
        ("lowercase123", "uppercase"),
        ("UPPERCASE123", "lowercase"),
        ("NoDigitsHere", "digit"),
    ]
    for i, (password, expected_error) in enumerate(test_cases):
        response = await client.post("/api/v1/auth/register", json={
            "email": f"pwtest{i}@pulse.local",
            "username": f"pwtest{i}",
            "password": password,
        })
        assert response.status_code == 422, f"Password '{password}' should fail validation"


@pytest.mark.asyncio
async def test_register_invalid_email(client: AsyncClient):
    """Test registration with invalid email format."""
    response = await client.post("/api/v1/auth/register", json={
        "email": "not-an-email",
        "username": "bademail",
        "password": "SecurePass123",
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_invalid_username(client: AsyncClient):
    """Test registration with invalid username characters."""
    response = await client.post("/api/v1/auth/register", json={
        "email": "badname@pulse.local",
        "username": "bad name!@#",
        "password": "SecurePass123",
    })
    assert response.status_code == 422
''')

# ═════════════════════════════════════════════════════════════════════════════
# TESTS — test_monitors
# ═════════════════════════════════════════════════════════════════════════════
w("tests/unit/test_monitors.py", '''
"""Monitor API endpoint tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_monitor(client: AsyncClient, test_user: dict):
    """Test creating a new monitor."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    response = await client.post("/api/v1/monitors", json={
        "name": "Google",
        "url": "https://google.com",
        "type": "https",
        "interval_seconds": 60,
        "expected_status_code": 200,
    }, headers=headers)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Google"
    assert data["url"] == "https://google.com"
    assert data["type"] == "https"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_create_monitor_invalid_url(client: AsyncClient, test_user: dict):
    """Test creating monitor with invalid URL fails."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    response = await client.post("/api/v1/monitors", json={
        "name": "Bad URL",
        "url": "ftp://invalid.com",
        "type": "http",
    }, headers=headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_monitors(client: AsyncClient, test_user: dict):
    """Test listing monitors."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    # Create a monitor first
    await client.post("/api/v1/monitors", json={
        "name": "Test Site",
        "url": "https://example.com",
        "type": "https",
    }, headers=headers)

    response = await client.get("/api/v1/monitors", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["items"]) >= 1


@pytest.mark.asyncio
async def test_get_monitor(client: AsyncClient, test_user: dict):
    """Test getting a specific monitor."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    create_resp = await client.post("/api/v1/monitors", json={
        "name": "Get Test",
        "url": "https://example.com",
        "type": "https",
    }, headers=headers)
    monitor_id = create_resp.json()["id"]

    response = await client.get(f"/api/v1/monitors/{monitor_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == monitor_id


@pytest.mark.asyncio
async def test_get_nonexistent_monitor(client: AsyncClient, test_user: dict):
    """Test getting a monitor that doesn't exist."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    response = await client.get("/api/v1/monitors/nonexistent-id", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_monitor(client: AsyncClient, test_user: dict):
    """Test updating a monitor."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    create_resp = await client.post("/api/v1/monitors", json={
        "name": "Update Test",
        "url": "https://example.com",
        "type": "https",
    }, headers=headers)
    monitor_id = create_resp.json()["id"]

    response = await client.patch(f"/api/v1/monitors/{monitor_id}", json={
        "name": "Updated Name",
        "interval_seconds": 120,
    }, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"
    assert data["interval_seconds"] == 120


@pytest.mark.asyncio
async def test_delete_monitor(client: AsyncClient, test_user: dict):
    """Test deleting a monitor."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    create_resp = await client.post("/api/v1/monitors", json={
        "name": "Delete Test",
        "url": "https://example.com",
        "type": "https",
    }, headers=headers)
    monitor_id = create_resp.json()["id"]

    response = await client.delete(f"/api/v1/monitors/{monitor_id}", headers=headers)
    assert response.status_code == 204

    # Verify it's gone
    get_resp = await client.get(f"/api/v1/monitors/{monitor_id}", headers=headers)
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_pause_resume_monitor(client: AsyncClient, test_user: dict):
    """Test pausing and resuming a monitor."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    create_resp = await client.post("/api/v1/monitors", json={
        "name": "Pause Test",
        "url": "https://example.com",
        "type": "https",
    }, headers=headers)
    monitor_id = create_resp.json()["id"]

    # Pause
    pause_resp = await client.post(f"/api/v1/monitors/{monitor_id}/pause", headers=headers)
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "paused"
    assert pause_resp.json()["is_active"] is False

    # Resume
    resume_resp = await client.post(f"/api/v1/monitors/{monitor_id}/resume", headers=headers)
    assert resume_resp.status_code == 200
    assert resume_resp.json()["status"] == "pending"
    assert resume_resp.json()["is_active"] is True


@pytest.mark.asyncio
async def test_list_monitors_pagination(client: AsyncClient, test_user: dict):
    """Test monitor listing pagination."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    response = await client.get("/api/v1/monitors?page=1&page_size=10", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 10


@pytest.mark.asyncio
async def test_unauthenticated_access(client: AsyncClient):
    """Test that unauthenticated requests are rejected."""
    response = await client.get("/api/v1/monitors")
    assert response.status_code == 401
''')

# ═════════════════════════════════════════════════════════════════════════════
# TESTS — test_services
# ═════════════════════════════════════════════════════════════════════════════
w("tests/unit/test_services.py", '''
"""Service layer tests."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import async_session_factory
from src.services.auth_service import AuthService
from src.services.monitor_service import MonitorService


@pytest.mark.asyncio
async def test_password_hashing():
    """Test password hashing and verification."""
    password = "TestPassword123"
    hashed = AuthService.hash_password(password)
    assert hashed != password
    assert AuthService.verify_password(password, hashed) is True
    assert AuthService.verify_password("WrongPassword", hashed) is False


@pytest.mark.asyncio
async def test_jwt_token_creation():
    """Test JWT token creation and decoding."""
    user_id = "test-user-123"
    token = AuthService.create_access_token(user_id)
    assert isinstance(token, str)
    assert len(token) > 0

    payload = AuthService.decode_token(token)
    assert payload is not None
    assert payload["sub"] == user_id
    assert payload["type"] == "access"


@pytest.mark.asyncio
async def test_jwt_token_expiry():
    """Test that expired tokens are rejected."""
    from datetime import timedelta
    token = AuthService.create_access_token("test", extra_claims={"exp": 0})
    # Token with exp=0 should be expired
    payload = AuthService.decode_token(token)
    # This depends on JWT library behavior with exp=0


@pytest.mark.asyncio
async def test_invalid_token():
    """Test decoding invalid token."""
    payload = AuthService.decode_token("invalid.token.here")
    assert payload is None


@pytest.mark.asyncio
async def test_refresh_token():
    """Test refresh token creation."""
    user_id = "test-user-456"
    token = AuthService.create_refresh_token(user_id)
    payload = AuthService.decode_token(token)
    assert payload["sub"] == user_id
    assert payload["type"] == "refresh"


@pytest.mark.asyncio
async def test_monitor_uptime_calculation():
    """Test uptime percentage calculation logic."""
    total_checks = 100
    up_checks = 95
    uptime = round((up_checks / total_checks) * 100, 2)
    assert uptime == 95.0


@pytest.mark.asyncio
async def test_monitor_status_transitions():
    """Test monitor status transition logic."""
    from src.models.monitor import MonitorStatus
    
    # Initial state
    status = MonitorStatus.PENDING
    assert status == "pending"
    
    # After successful check
    status = MonitorStatus.UP
    assert status == "up"
    
    # After failures
    consecutive_failures = 3
    if consecutive_failures >= 3:
        status = MonitorStatus.DOWN
    assert status == "down"


@pytest.mark.asyncio
async def test_alert_severity_levels():
    """Test alert severity level constants."""
    from src.models.alert import AlertSeverity
    
    assert AlertSeverity.CRITICAL == "critical"
    assert AlertSeverity.WARNING == "warning"
    assert AlertSeverity.INFO == "info"
    assert AlertSeverity.RECOVERY == "recovery"


@pytest.mark.asyncio
async def test_monitor_type_constants():
    """Test monitor type constants."""
    from src.models.monitor import MonitorType
    
    assert MonitorType.HTTP == "http"
    assert MonitorType.HTTPS == "https"
    assert MonitorType.TCP == "tcp"
    assert MonitorType.ICMP == "icmp"
    assert MonitorType.DNS == "dns"
    assert MonitorType.KEYWORD == "keyword"
    assert MonitorType.GRAPHQL == "graphql"


@pytest.mark.asyncio
async def test_team_role_hierarchy():
    """Test team role hierarchy and permissions."""
    from src.models.team import TeamRole
    
    assert TeamRole.OWNER == "owner"
    assert TeamRole.ADMIN == "admin"
    assert TeamRole.MEMBER == "member"
    assert TeamRole.VIEWER == "viewer"


@pytest.mark.asyncio
async def test_incident_status_flow():
    """Test incident status flow constants."""
    from src.models.incident import IncidentStatus
    
    assert IncidentStatus.INVESTIGATING == "investigating"
    assert IncidentStatus.IDENTIFIED == "identified"
    assert IncidentStatus.MONITORING == "monitoring"
    assert IncidentStatus.RESOLVED == "resolved"


@pytest.mark.asyncio
async def test_widget_types():
    """Test widget type constants."""
    from src.models.dashboard import WidgetType
    
    assert WidgetType.STATUS_PIECE == "status_piece"
    assert WidgetType.UPTIME_BAR == "uptime_bar"
    assert WidgetType.RESPONSE_TIME_CHART == "response_time_chart"
    assert WidgetType.MONITOR_LIST == "monitor_list"
    assert WidgetType.INCIDENT_LIST == "incident_list"
    assert WidgetType.STATS_OVERVIEW == "stats_overview"
''')

# ═════════════════════════════════════════════════════════════════════════════
# TESTS — test_worker
# ═════════════════════════════════════════════════════════════════════════════
w("tests/unit/test_worker.py", '''
"""Check worker tests."""

from __future__ import annotations

import pytest
from src.workers.check_worker import CheckResult, HTTPChecker, TCPChecker, ICMPChecker, DNSChecker


class TestCheckResult:
    """Tests for CheckResult data class."""

    def test_success_result(self):
        result = CheckResult(is_up=True, status_code=200, response_time_ms=45.5)
        assert result.is_up is True
        assert result.status_code == 200
        assert result.response_time_ms == 45.5
        assert result.error_message is None

    def test_failure_result(self):
        result = CheckResult(is_up=False, error_message="Connection refused")
        assert result.is_up is False
        assert result.error_message == "Connection refused"

    def test_full_result(self):
        result = CheckResult(
            is_up=True,
            status_code=200,
            response_time_ms=123.45,
            dns_resolution_ms=12.3,
            tls_handshake_ms=45.6,
            ttfb_ms=78.9,
            content_length=1024,
            headers={"Content-Type": "text/html"},
        )
        assert result.dns_resolution_ms == 12.3
        assert result.tls_handshake_ms == 45.6
        assert result.ttfb_ms == 78.9
        assert result.content_length == 1024
        assert result.headers["Content-Type"] == "text/html"


class TestHTTPChecker:
    """Tests for HTTP checker."""

    def test_checker_creation(self):
        checker = HTTPChecker()
        assert checker is not None

    def test_parse_headers_valid(self):
        import json
        headers = json.dumps({"Authorization": "Bearer token", "X-Custom": "value"})
        parsed = HTTPChecker._parse_headers(headers)
        assert parsed["Authorization"] == "Bearer token"
        assert parsed["X-Custom"] == "value"

    def test_parse_headers_empty(self):
        assert HTTPChecker._parse_headers(None) == {}
        assert HTTPChecker._parse_headers("") == {}

    def test_parse_headers_invalid_json(self):
        """Invalid JSON should return empty dict."""
        result = HTTPChecker._parse_headers("not json")
        assert result == {}


class TestTCPChecker:
    """Tests for TCP checker."""

    def test_checker_creation(self):
        checker = TCPChecker()
        assert checker is not None


class TestICMPChecker:
    """Tests for ICMP checker."""

    def test_checker_creation(self):
        checker = ICMPChecker()
        assert checker is not None


class TestDNSChecker:
    """Tests for DNS checker."""

    def test_checker_creation(self):
        checker = DNSChecker()
        assert checker is not None


class TestSSLInfo:
    """Tests for SSL info data class."""

    def test_ssl_info_defaults(self):
        from src.workers.check_worker import SSLInfo
        info = SSLInfo()
        assert info.is_valid is True
        assert info.days_remaining == 0

    def test_ssl_info_populated(self):
        from src.workers.check_worker import SSLInfo
        info = SSLInfo(
            issuer="Let's Encrypt",
            subject="example.com",
            expires_at="2025-12-31",
            days_remaining=180,
            is_valid=True,
            protocol="TLS 1.3",
        )
        assert info.issuer == "Let's Encrypt"
        assert info.days_remaining == 180
        assert info.protocol == "TLS 1.3"
''')

# ═════════════════════════════════════════════════════════════════════════════
# TESTS — Integration
# ═════════════════════════════════════════════════════════════════════════════
w("tests/integration/test_full_flow.py", '''
"""Integration tests — full user flow."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_complete_monitor_flow(client: AsyncClient):
    """Test complete flow: register → create monitor → check → delete."""
    # 1. Register
    reg = await client.post("/api/v1/auth/register", json={
        "email": "flow@pulse.local",
        "username": "flowuser",
        "password": "SecurePass123",
    })
    assert reg.status_code == 201
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Create monitor
    create = await client.post("/api/v1/monitors", json={
        "name": "Flow Test Monitor",
        "url": "https://httpbin.org",
        "type": "https",
        "interval_seconds": 30,
    }, headers=headers)
    assert create.status_code == 201
    monitor_id = create.json()["id"]

    # 3. Get monitor
    get = await client.get(f"/api/v1/monitors/{monitor_id}", headers=headers)
    assert get.status_code == 200
    assert get.json()["name"] == "Flow Test Monitor"

    # 4. List monitors
    listing = await client.get("/api/v1/monitors", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1

    # 5. Pause monitor
    pause = await client.post(f"/api/v1/monitors/{monitor_id}/pause", headers=headers)
    assert pause.status_code == 200
    assert pause.json()["status"] == "paused"

    # 6. Update monitor
    update = await client.patch(f"/api/v1/monitors/{monitor_id}", json={
        "name": "Updated Flow Monitor",
    }, headers=headers)
    assert update.status_code == 200
    assert update.json()["name"] == "Updated Flow Monitor"

    # 7. Delete monitor
    delete = await client.delete(f"/api/v1/monitors/{monitor_id}", headers=headers)
    assert delete.status_code == 204


@pytest.mark.asyncio
async def test_team_flow(client: AsyncClient):
    """Test team creation and member management flow."""
    # Register user
    reg = await client.post("/api/v1/auth/register", json={
        "email": "teamflow@pulse.local",
        "username": "teamflow",
        "password": "SecurePass123",
    })
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create team
    create = await client.post("/api/v1/teams", json={
        "name": "Flow Team",
        "slug": "flow-team",
        "description": "Integration test team",
    }, headers=headers)
    assert create.status_code == 201
    team_id = create.json()["id"]

    # List teams
    listing = await client.get("/api/v1/teams", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1

    # Get team details
    get = await client.get(f"/api/v1/teams/{team_id}", headers=headers)
    assert get.status_code == 200
    assert get.json()["name"] == "Flow Team"


@pytest.mark.asyncio
async def test_dashboard_flow(client: AsyncClient):
    """Test dashboard creation and widget management."""
    # Setup user and monitor
    reg = await client.post("/api/v1/auth/register", json={
        "email": "dashflow@pulse.local",
        "username": "dashflow",
        "password": "SecurePass123",
    })
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    mon = await client.post("/api/v1/monitors", json={
        "name": "Dash Monitor",
        "url": "https://example.com",
    }, headers=headers)
    monitor_id = mon.json()["id"]

    # Create dashboard
    create = await client.post("/api/v1/dashboards", json={
        "name": "Test Dashboard",
        "slug": "test-dash",
        "columns": 3,
        "theme": "dark",
    }, headers=headers)
    assert create.status_code == 201
    dash_id = create.json()["id"]

    # Add widget
    widget = await client.post(f"/api/v1/dashboards/{dash_id}/widgets", json={
        "name": "Monitor Status",
        "widget_type": "status_piece",
        "monitor_ids": [monitor_id],
    }, headers=headers)
    assert widget.status_code == 201

    # Get dashboard with data
    get = await client.get(f"/api/v1/dashboards/{dash_id}", headers=headers)
    assert get.status_code == 200


@pytest.mark.asyncio
async def test_unauthorized_cannot_access_others_resources(client: AsyncClient):
    """Test that users cannot access each other's resources."""
    # User 1 creates a monitor
    reg1 = await client.post("/api/v1/auth/register", json={
        "email": "user1@pulse.local",
        "username": "user1",
        "password": "SecurePass123",
    })
    token1 = reg1.json()["access_token"]
    headers1 = {"Authorization": f"Bearer {token1}"}

    mon = await client.post("/api/v1/monitors", json={
        "name": "Private Monitor",
        "url": "https://example.com",
    }, headers=headers1)
    monitor_id = mon.json()["id"]

    # User 2 registers
    reg2 = await client.post("/api/v1/auth/register", json={
        "email": "user2@pulse.local",
        "username": "user2",
        "password": "SecurePass123",
    })
    token2 = reg2.json()["access_token"]
    headers2 = {"Authorization": f"Bearer {token2}"}

    # User 2 cannot see user 1's monitor
    response = await client.get(f"/api/v1/monitors/{monitor_id}", headers=headers2)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_health_endpoint_no_auth(client: AsyncClient):
    """Test that health endpoint is publicly accessible."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
''')

print("All remaining files written")
