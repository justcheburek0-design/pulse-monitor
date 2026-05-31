"""Incident management — tracks downtime events."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import String, Boolean, DateTime, Text, Integer, ForeignKey, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.config.database import Base

if TYPE_CHECKING:
    from src.models.user import User
    from src.models.monitor import Monitor
    from src.models.alert import Alert


class IncidentStatus:
    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class IncidentSeverity:
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    MAINTENANCE = "maintenance"


class Incident(Base):
    """A downtime or degradation event."""

    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    monitor_id: Mapped[str] = mapped_column(String(36), ForeignKey("monitors.id"), nullable=False, index=True)
    
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default=IncidentSeverity.MAJOR)
    status: Mapped[str] = mapped_column(String(20), default=IncidentStatus.INVESTIGATING)
    
    # Timeline
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    identified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Root cause
    root_cause: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Metadata
    affected_regions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array
    created_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relations
    monitor: Mapped["Monitor"] = relationship("Monitor")
    events: Mapped[List["IncidentEvent"]] = relationship("IncidentEvent", back_populates="incident", lazy="selectin", order_by="IncidentEvent.created_at")
    alerts: Mapped[List["Alert"]] = relationship("Alert", back_populates="incident")

    def __repr__(self) -> str:
        return f"<Incident {self.severity}: {self.title[:50]} ({self.status})>"

    @property
    def is_resolved(self) -> bool:
        return self.status == IncidentStatus.RESOLVED

    @property
    def is_active(self) -> bool:
        return self.status != IncidentStatus.RESOLVED

    def calculate_duration(self) -> Optional[int]:
        if self.resolved_at:
            delta = self.resolved_at - self.started_at
            return int(delta.total_seconds())
        return None


class IncidentEvent(Base):
    """An update/comment on an incident."""

    __tablename__ = "incident_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    incident_id: Mapped[str] = mapped_column(String(36), ForeignKey("incidents.id"), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)  # created, status_change, comment, resolved, identified
    message: Mapped[str] = mapped_column(Text, nullable=False)
    
    old_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    new_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    # Relations
    incident: Mapped["Incident"] = relationship("Incident", back_populates="events")

    def __repr__(self) -> str:
        return f"<IncidentEvent {self.event_type}: {self.message[:50]}>"
