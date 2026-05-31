"""Monitor CRUD and management service."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.monitor import Monitor, MonitorCheck, MonitorStatus, MonitorType
from src.models.user import User


class MonitorService:
    """CRUD operations for monitors."""

    @staticmethod
    async def create_monitor(
        db: AsyncSession,
        owner_id: str,
        name: str,
        url: str,
        monitor_type: str = MonitorType.HTTPS,
        interval_seconds: int = 60,
        timeout_seconds: int = 10,
        retries: int = 3,
        description: Optional[str] = None,
        method: str = "GET",
        headers: Optional[str] = None,
        expected_status_code: Optional[int] = 200,
        expected_keyword: Optional[str] = None,
        verify_ssl: bool = True,
        follow_redirects: bool = True,
    ) -> Monitor:
        # Check limit
        count = await db.execute(
            select(func.count()).select_from(Monitor).where(Monitor.owner_id == owner_id)
        )
        current = count.scalar_one()
        user_result = await db.execute(select(User).where(User.id == owner_id))
        user = user_result.scalar_one()
        if current >= user.monitors_limit:
            raise ValueError(f"Monitor limit reached ({user.monitors_limit})")

        monitor = Monitor(
            owner_id=owner_id,
            name=name,
            url=url,
            type=monitor_type,
            interval_seconds=interval_seconds,
            timeout_seconds=timeout_seconds,
            retries=retries,
            description=description,
            method=method,
            headers=headers,
            expected_status_code=expected_status_code,
            expected_keyword=expected_keyword,
            verify_ssl=verify_ssl,
            follow_redirects=follow_redirects,
        )
        db.add(monitor)
        await db.flush()
        return monitor

    @staticmethod
    async def get_monitor(db: AsyncSession, monitor_id: str, owner_id: str) -> Optional[Monitor]:
        result = await db.execute(
            select(Monitor).where(Monitor.id == monitor_id, Monitor.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_user_monitors(
        db: AsyncSession,
        owner_id: str,
        skip: int = 0,
        limit: int = 50,
        status_filter: Optional[str] = None,
    ) -> Tuple[List[Monitor], int]:
        query = select(Monitor).where(Monitor.owner_id == owner_id)
        count_query = select(func.count()).select_from(Monitor).where(Monitor.owner_id == owner_id)
        
        if status_filter:
            query = query.where(Monitor.status == status_filter)
            count_query = count_query.where(Monitor.status == status_filter)

        total = (await db.execute(count_query)).scalar_one()
        query = query.order_by(Monitor.created_at.desc()).offset(skip).limit(limit)
        result = await db.execute(query)
        return result.scalars().all(), total

    @staticmethod
    async def update_monitor(db: AsyncSession, monitor: Monitor, **kwargs) -> Monitor:
        allowed = {
            "name", "url", "type", "interval_seconds", "timeout_seconds",
            "retries", "description", "method", "headers", "expected_status_code",
            "verify_ssl", "follow_redirects", "is_active", "is_public",
            "expected_keyword", "port", "body",
        }
        for key, value in kwargs.items():
            if key in allowed and value is not None:
                setattr(monitor, key, value)
        await db.flush()
        await db.refresh(monitor)
        return monitor

    @staticmethod
    async def delete_monitor(db: AsyncSession, monitor: Monitor) -> None:
        await db.delete(monitor)
        await db.flush()

    @staticmethod
    async def pause_monitor(db: AsyncSession, monitor: Monitor) -> Monitor:
        monitor.is_active = False
        monitor.status = MonitorStatus.PAUSED
        monitor.paused_at = datetime.utcnow()
        await db.flush()
        await db.refresh(monitor)
        return monitor

    @staticmethod
    async def resume_monitor(db: AsyncSession, monitor: Monitor) -> Monitor:
        monitor.is_active = True
        monitor.status = MonitorStatus.PENDING
        monitor.paused_at = None
        await db.flush()
        await db.refresh(monitor)
        return monitor

    # ── Check results ─────────────────────────────────────────────────────

    @staticmethod
    async def record_check(
        db: AsyncSession,
        monitor_id: str,
        is_up: bool,
        response_time_ms: float,
        status_code: Optional[int] = None,
        error_message: Optional[str] = None,
        dns_ms: Optional[float] = None,
        tls_ms: Optional[float] = None,
        ttfb_ms: Optional[float] = None,
        content_length: Optional[int] = None,
    ) -> MonitorCheck:
        check = MonitorCheck(
            monitor_id=monitor_id,
            is_up=is_up,
            response_time_ms=response_time_ms,
            status_code=status_code,
            error_message=error_message,
            dns_resolution_ms=dns_ms,
            tls_handshake_ms=tls_ms,
            ttfb_ms=ttfb_ms,
            content_length=content_length,
        )
        db.add(check)

        # Update monitor status
        monitor = await db.get(Monitor, monitor_id)
        if monitor:
            monitor.last_check_at = datetime.utcnow()
            monitor.avg_response_time_ms = (monitor.avg_response_time_ms + response_time_ms) / 2

            if is_up:
                monitor.last_up_at = datetime.utcnow()
                monitor.consecutive_failures = 0
                monitor.status = MonitorStatus.UP
            else:
                monitor.last_down_at = datetime.utcnow()
                monitor.consecutive_failures += 1
                if monitor.consecutive_failures >= monitor.retries:
                    monitor.status = MonitorStatus.DOWN

            # Recalculate uptime (simplified)
            total_checks = await db.execute(
                select(func.count()).select_from(MonitorCheck).where(MonitorCheck.monitor_id == monitor_id)
            )
            up_checks = await db.execute(
                select(func.count()).select_from(MonitorCheck).where(
                    MonitorCheck.monitor_id == monitor_id, MonitorCheck.is_up == True
                )
            )
            total = total_checks.scalar_one()
            up = up_checks.scalar_one()
            if total > 0:
                monitor.uptime_percentage = round((up / total) * 100, 2)

        await db.flush()
        return check

    @staticmethod
    async def get_checks(
        db: AsyncSession,
        monitor_id: str,
        hours: int = 24,
        limit: int = 1000,
    ) -> List[MonitorCheck]:
        since = datetime.utcnow() - timedelta(hours=hours)
        result = await db.execute(
            select(MonitorCheck).where(
                MonitorCheck.monitor_id == monitor_id,
                MonitorCheck.checked_at >= since,
            ).order_by(MonitorCheck.checked_at.desc()).limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def get_monitor_checks(db: AsyncSession, monitor_id: str, limit: int = 50) -> List[MonitorCheck]:
        return await MonitorService.get_checks(db, monitor_id, hours=720, limit=limit)

    @staticmethod
    async def get_uptime_stats(
        db: AsyncSession,
        monitor_id: str,
        days: int = 30,
    ) -> dict:
        since = datetime.utcnow() - timedelta(days=days)
        total = await db.execute(
            select(func.count()).select_from(MonitorCheck).where(
                MonitorCheck.monitor_id == monitor_id,
                MonitorCheck.checked_at >= since,
            )
        )
        up = await db.execute(
            select(func.count()).select_from(MonitorCheck).where(
                MonitorCheck.monitor_id == monitor_id,
                MonitorCheck.checked_at >= since,
                MonitorCheck.is_up == True,
            )
        )
        avg_rt = await db.execute(
            select(func.avg(MonitorCheck.response_time_ms)).where(
                MonitorCheck.monitor_id == monitor_id,
                MonitorCheck.checked_at >= since,
                MonitorCheck.is_up == True,
            )
        )
        
        total_count = total.scalar_one()
        up_count = up.scalar_one()
        avg_response = avg_rt.scalar_one() or 0

        return {
            "period_days": days,
            "total_checks": total_count,
            "up_checks": up_count,
            "down_checks": total_count - up_count,
            "uptime_percentage": round((up_count / total_count) * 100, 2) if total_count > 0 else 100.0,
            "avg_response_time_ms": round(avg_response, 2),
        }

    @staticmethod
    async def get_monitors_to_check(db: AsyncSession) -> List[Monitor]:
        """Get all active monitors that need checking."""
        result = await db.execute(
            select(Monitor).where(
                Monitor.is_active == True,
                Monitor.status != MonitorStatus.PAUSED,
            )
        )
        return result.scalars().all()
