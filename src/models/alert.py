"""Alert rules and notification channels."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import String, Boolean, DateTime, Text, Integer, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.config.database import Base

if TYPE_CHECKING:
    from src.models.user import User
    from src.models.monitor import Monitor
    from src.models.incident import Incident


class AlertChannelType:
    EMAIL = "email"
    SLACK = "slack"
    TELEGRAM = "telegram"
    WEBHOOK = "webhook"
    SMS = "sms"
    DISCORD = "discord"
    PAGERDUTY = "pagerduty"


class AlertSeverity:
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
    RECOVERY = "recovery"


class AlertChannel(Base):
    """Notification channel configuration."""

    __tablename__ = "alert_channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(20), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Channel-specific config (stored as JSON)
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    # For email
    email_address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # For Slack/Discord
    webhook_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    # For Telegram
    chat_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # For SMS
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Relations
    user: Mapped["User"] = relationship("User")
    alert_rules: Mapped[List["AlertRule"]] = relationship("AlertRule", back_populates="channel")

    def __repr__(self) -> str:
        return f"<AlertChannel {self.name} ({self.channel_type})>"


class AlertRule(Base):
    """Defines when and how to send alerts for a monitor."""

    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    monitor_id: Mapped[str] = mapped_column(String(36), ForeignKey("monitors.id"), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(String(36), ForeignKey("alert_channels.id"), nullable=False)
    
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default=AlertSeverity.CRITICAL)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Trigger conditions
    trigger_on_down: Mapped[bool] = mapped_column(Boolean, default=True)
    trigger_on_up: Mapped[bool] = mapped_column(Boolean, default=True)  # recovery notification
    trigger_on_ssl_expiry: Mapped[bool] = mapped_column(Boolean, default=False)
    ssl_expiry_days_before: Mapped[int] = mapped_column(Integer, default=14)
    trigger_on_keyword_absent: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Threshold
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=2)
    response_time_threshold_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Rate limiting
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=15)
    max_alerts_per_hour: Mapped[int] = mapped_column(Integer, default=10)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Relations
    monitor: Mapped["Monitor"] = relationship("Monitor", back_populates="alert_rules")
    channel: Mapped["AlertChannel"] = relationship("AlertChannel", back_populates="alert_rules")
    alerts: Mapped[List["Alert"]] = relationship("Alert", back_populates="rule")

    def __repr__(self) -> str:
        return f"<AlertRule {self.name} ({self.severity})>"


class Alert(Base):
    """A fired alert instance."""

    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    rule_id: Mapped[str] = mapped_column(String(36), ForeignKey("alert_rules.id"), nullable=False, index=True)
    incident_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("incidents.id"), nullable=True)
    
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="firing")  # firing, sent, acknowledged, resolved, failed
    
    # Delivery info
    sent_via: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    sent_to: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    delivery_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Response tracking
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Relations
    rule: Mapped["AlertRule"] = relationship("AlertRule", back_populates="alerts")
    incident: Mapped[Optional["Incident"]] = relationship("Incident", back_populates="alerts")

    def __repr__(self) -> str:
        return f"<Alert {self.severity}: {self.title[:50]}>"
