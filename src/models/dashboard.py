"""Dashboard and widget configurations."""

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


class Dashboard(Base):
    """User-customizable monitoring dashboard."""

    __tablename__ = "dashboards"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    team_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("teams.id"), nullable=True)
    
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Layout
    columns: Mapped[int] = mapped_column(Integer, default=3)
    theme: Mapped[str] = mapped_column(String(20), default="dark")
    refresh_interval_seconds: Mapped[int] = mapped_column(Integer, default=30)
    
    # Status
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    public_slug: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relations
    user: Mapped["User"] = relationship("User")
    widgets: Mapped[List["DashboardWidget"]] = relationship("DashboardWidget", back_populates="dashboard", lazy="selectin", order_by="DashboardWidget.position")

    def __repr__(self) -> str:
        return f"<Dashboard {self.name}>"


class WidgetType:
    STATUS_PIECE = "status_piece"
    UPTIME_BAR = "uptime_bar"
    RESPONSE_TIME_CHART = "response_time_chart"
    MONITOR_LIST = "monitor_list"
    INCIDENT_LIST = "incident_list"
    STATS_OVERVIEW = "stats_overview"
    LATENCY_MAP = "latency_map"
    SSL_STATUS = "ssl_status"
    TEXT = "text"
    METRIC_CARD = "metric_card"


class DashboardWidget(Base):
    """A widget on a dashboard."""

    __tablename__ = "dashboard_widgets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dashboard_id: Mapped[str] = mapped_column(String(36), ForeignKey("dashboards.id"), nullable=False)
    
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    widget_type: Mapped[str] = mapped_column(String(30), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    
    # Layout
    width: Mapped[int] = mapped_column(Integer, default=1)  # columns spanned
    height: Mapped[int] = mapped_column(Integer, default=1)  # rows spanned
    
    # Widget configuration (type-specific)
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    # Filter by monitors
    monitor_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)  # null = all monitors
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relations
    dashboard: Mapped["Dashboard"] = relationship("Dashboard", back_populates="widgets")

    def __repr__(self) -> str:
        return f"<Widget {self.name} ({self.widget_type})>"
