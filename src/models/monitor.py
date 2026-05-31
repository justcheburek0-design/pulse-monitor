"""Monitor model — HTTP/TCP/ICMP checks and their results."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    String, Boolean, DateTime, Text, Integer, Float, ForeignKey, Enum as SAEnum
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.config.database import Base

if TYPE_CHECKING:
    from src.models.user import User
    from src.models.alert import AlertRule


class MonitorStatus:
    """Monitor status constants."""
    PENDING = "pending"
    UP = "up"
    DOWN = "down"
    PAUSED = "paused"
    ERROR = "error"
    MAINTENANCE = "maintenance"


class MonitorType:
    """Monitor type constants."""
    HTTP = "http"
    HTTPS = "https"
    TCP = "tcp"
    ICMP = "icmp"
    DNS = "dns"
    KEYWORD = "keyword"
    GRAPHQL = "graphql"


class Monitor(Base):
    """A monitoring check configuration."""

    __tablename__ = "monitors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    
    # Basic info
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False, default=MonitorType.HTTPS)
    
    # Target
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Check configuration
    interval_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    retry_delay_seconds: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    
    # HTTP-specific
    method: Mapped[str] = mapped_column(String(10), default="GET")
    headers: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expected_status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    expected_keyword: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    follow_redirects: Mapped[bool] = mapped_column(Boolean, default=True)
    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Status
    status: Mapped[str] = mapped_column(String(20), default=MonitorStatus.PENDING, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Statistics
    uptime_percentage: Mapped[float] = mapped_column(Float, default=100.0)
    avg_response_time_ms: Mapped[float] = mapped_column(Float, default=0.0)
    last_check_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_up_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_down_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    paused_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Relations
    owner: Mapped["User"] = relationship("User", back_populates="monitors")
    checks: Mapped[List["MonitorCheck"]] = relationship("MonitorCheck", back_populates="monitor", lazy="selectin", order_by="MonitorCheck.checked_at.desc()")
    alert_rules: Mapped[List["AlertRule"]] = relationship("AlertRule", back_populates="monitor", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Monitor {self.name} ({self.type}) — {self.status}>"


class MonitorCheck(Base):
    """Result of a single monitor check."""

    __tablename__ = "monitor_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    monitor_id: Mapped[str] = mapped_column(String(36), ForeignKey("monitors.id"), nullable=False, index=True)
    
    # Result
    is_up: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[float] = mapped_column(Float, default=0.0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Details
    dns_resolution_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tls_handshake_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ttfb_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # time to first byte
    content_length: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    headers: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    
    # Check metadata
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    region: Mapped[str] = mapped_column(String(50), default="default")
    
    # Relation
    monitor: Mapped["Monitor"] = relationship("Monitor", back_populates="checks")

    def __repr__(self) -> str:
        status = "UP" if self.is_up else "DOWN"
        return f"<Check {self.monitor_id} — {status} ({self.response_time_ms}ms)>"
