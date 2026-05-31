"""Team management — shared access to monitors."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import String, Boolean, DateTime, Text, Integer, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.config.database import Base

if TYPE_CHECKING:
    from src.models.user import User


class TeamRole:
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class Team(Base):
    """A team that can share monitors."""

    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    
    # Settings
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    default_role: Mapped[str] = mapped_column(String(20), default=TeamRole.VIEWER)
    
    # Limits
    max_members: Mapped[int] = mapped_column(Integer, default=5)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relations
    owner: Mapped["User"] = relationship("User", back_populates="teams_owned")
    members: Mapped[List["TeamMember"]] = relationship("TeamMember", back_populates="team", lazy="selectin")
    invites: Mapped[List["TeamInvite"]] = relationship("TeamInvite", back_populates="team", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Team {self.name}>"


class TeamMember(Base):
    """A user's membership in a team."""

    __tablename__ = "team_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    team_id: Mapped[str] = mapped_column(String(36), ForeignKey("teams.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    
    role: Mapped[str] = mapped_column(String(20), default=TeamRole.MEMBER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Notification preferences
    notify_on_down: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_on_up: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_on_ssl: Mapped[bool] = mapped_column(Boolean, default=False)
    
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    invited_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    
    # Relations
    team: Mapped["Team"] = relationship("Team", back_populates="members")
    user: Mapped["User"] = relationship("User", back_populates="team_memberships")

    def __repr__(self) -> str:
        return f"<TeamMember {self.user_id} in {self.team_id} ({self.role})>"

    @property
    def is_owner(self) -> bool:
        return self.role == TeamRole.OWNER

    @property
    def is_admin(self) -> bool:
        return self.role in (TeamRole.OWNER, TeamRole.ADMIN)

    @property
    def can_edit(self) -> bool:
        return self.role in (TeamRole.OWNER, TeamRole.ADMIN)

    @property
    def can_manage_members(self) -> bool:
        return self.role in (TeamRole.OWNER, TeamRole.ADMIN)


class TeamInvite(Base):
    """Pending team invitation."""

    __tablename__ = "team_invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    team_id: Mapped[str] = mapped_column(String(36), ForeignKey("teams.id"), nullable=False)
    invited_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default=TeamRole.MEMBER)
    token: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    
    is_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Relations
    team: Mapped["Team"] = relationship("Team", back_populates="invites")

    def __repr__(self) -> str:
        return f"<TeamInvite {self.email} to {self.team_id}>"

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at
