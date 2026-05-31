"""Alert and alert rule API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ConfigDict, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.api.middleware.auth import get_current_user
from src.models.alert import AlertChannel, AlertRule, Alert, AlertChannelType, AlertSeverity
from src.services.alert_service import AlertService

router = APIRouter(prefix="/alerts", tags=["Alerts"])



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

    model_config = ConfigDict(from_attributes=True)



    @field_serializer('last_used_at', 'created_at')
    def serialize_dt(self, dt, _info):
        return dt.isoformat() if dt else None

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

    model_config = ConfigDict(from_attributes=True)



    @field_serializer('last_triggered_at', 'created_at')
    def serialize_dt(self, dt, _info):
        return dt.isoformat() if dt else None

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

    model_config = ConfigDict(from_attributes=True)


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
