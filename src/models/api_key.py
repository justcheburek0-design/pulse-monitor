"""API key management for programmatic access."""

from __future__ import annotations

import uuid
import secrets
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import String, Boolean, DateTime, Text, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.config.database import Base

if TYPE_CHECKING:
    from src.models.user import User


class ApiKey(Base):
    """API key for programmatic access."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(8), nullable=False)  # First 8 chars for display
    hashed_key: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Permissions
    can_read: Mapped[bool] = mapped_column(Boolean, default=True)
    can_write: Mapped[bool] = mapped_column(Boolean, default=True)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False)
    can_manage_monitors: Mapped[bool] = mapped_column(Boolean, default=True)
    can_manage_alerts: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Rate limiting
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    
    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Tracking
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Expiry
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Relation
    user: Mapped["User"] = relationship("User", back_populates="api_keys")

    def __repr__(self) -> str:
        return f"<ApiKey {self.name} ({self.key_prefix}***)>"

    @staticmethod
    def generate_key() -> tuple[str, str]:
        """Generate a new API key. Returns (plain_key, hashed_key)."""
        plain = secrets.token_urlsafe(48)
        # In production, this would be hashed with bcrypt
        import hashlib
        hashed = hashlib.sha256(plain.encode()).hexdigest()
        prefix = plain[:8]
        return plain, hashed, prefix

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    @property
    def is_valid(self) -> bool:
        return self.is_active and not self.is_expired and self.revoked_at is None
