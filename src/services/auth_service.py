"""Authentication service — registration, login, token management."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.models.user import User

settings = get_settings()


def _hash_password(password: str) -> str:
    """Hash password using bcrypt directly (passlib incompatible with bcrypt 5.x)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against bcrypt hash."""
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


class AuthService:
    """Handles all authentication operations."""

    @staticmethod
    def hash_password(password: str) -> str:
        return _hash_password(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        return _verify_password(plain_password, hashed_password)

    @staticmethod
    def create_access_token(user_id: int, extra_claims: Optional[dict] = None) -> str:
        expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
        payload = {
            "sub": str(user_id),
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "access",
        }
        if extra_claims:
            payload.update(extra_claims)
        return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)

    @staticmethod
    def create_refresh_token(user_id: int) -> str:
        expire = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)
        payload = {
            "sub": str(user_id),
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "refresh",
        }
        return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)

    @staticmethod
    def decode_token(token: str) -> Optional[dict]:
        try:
            payload = jwt.decode(token, settings.secret_key.get_secret_value(), algorithms=[settings.algorithm])
            return payload
        except JWTError:
            return None

    @staticmethod
    async def register_user(
        db: AsyncSession,
        email: str,
        username: str,
        password: str,
        full_name: Optional[str] = None,
    ) -> Tuple[User, str]:
        # Check existing
        existing = await db.execute(
            select(User).where((User.email == email) | (User.username == username))
        )
        if existing.scalar_one_or_none():
            raise ValueError("User with this email or username already exists")

        user = User(
            email=email.lower().strip(),
            username=username.lower().strip(),
            hashed_password=AuthService.hash_password(password),
            full_name=full_name,
        )
        db.add(user)
        await db.flush()

        token = AuthService.create_access_token(user.id)
        return user, token

    @staticmethod
    async def authenticate_user(
        db: AsyncSession,
        email_or_username: str,
        password: str,
    ) -> Optional[Tuple[User, str]]:
        result = await db.execute(
            select(User).where(
                (User.email == email_or_username.lower()) | (User.username == email_or_username.lower())
            )
        )
        user = result.scalar_one_or_none()
        if not user or not AuthService.verify_password(password, user.hashed_password):
            return None
        if not user.is_active:
            return None

        # Update last login
        user.last_login_at = datetime.utcnow()
        token = AuthService.create_access_token(user.id)
        return user, token

    @staticmethod
    async def get_user_by_id(db: AsyncSession, user_id: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    def generate_email_verification_token(user_id: str) -> str:
        expire = datetime.utcnow() + timedelta(hours=24)
        payload = {"sub": user_id, "exp": expire, "type": "email_verify"}
        return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)

    @staticmethod
    def generate_password_reset_token(email: str) -> str:
        expire = datetime.utcnow() + timedelta(hours=1)
        payload = {"sub": email, "exp": expire, "type": "password_reset"}
        return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)

    @staticmethod
    async def change_password(db: AsyncSession, user: User, new_password: str) -> None:
        user.hashed_password = AuthService.hash_password(new_password)
        await db.flush()

    @staticmethod
    async def verify_email(db: AsyncSession, user_id: str) -> None:
        user = await AuthService.get_user_by_id(db, user_id)
        if user:
            user.is_verified = True
            await db.flush()
