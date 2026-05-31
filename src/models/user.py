"""User model — authentication and profile."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import String, Boolean, DateTime, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.config.database import Base

if TYPE_CHECKING:
    from src.models.monitor import Monitor
    from src.models.team import Team, TeamMember
    from src.models.api_key import ApiKey


class User(Base):
    """Registered user account."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Plan
    plan: Mapped[str] = mapped_column(String(20), default="free", nullable=False)  # free, pro, enterprise
    monitors_limit: Mapped[int] = mapped_column(Integer, default=10)
    team_members_limit: Mapped[int] = mapped_column(Integer, default=3)
    data_retention_days: Mapped[int] = mapped_column(Integer, default=30)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Relations
    monitors: Mapped[List["Monitor"]] = relationship("Monitor", back_populates="owner", lazy="selectin")
    teams_owned: Mapped[List["Team"]] = relationship("Team", back_populates="owner", lazy="selectin")
    team_memberships: Mapped[List["TeamMember"]] = relationship("TeamMember", back_populates="user", lazy="selectin")
    api_keys: Mapped[List["ApiKey"]] = relationship("ApiKey", back_populates="user", lazy="selectin")

    def __repr__(self) -> str:
        return f"<User {self.username} ({self.email})>"

    @property
    def is_pro(self) -> bool:
        return self.plan in ("pro", "enterprise")

    @property
    def is_enterprise(self) -> bool:
        return self.plan == "enterprise"
