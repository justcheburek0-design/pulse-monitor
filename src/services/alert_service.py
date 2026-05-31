"""Alert processing service."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.models.alert import Alert, AlertRule, AlertChannel, AlertSeverity, AlertChannelType
from src.models.monitor import Monitor, MonitorCheck
from src.services.notification_service import NotificationService

logger = logging.getLogger("pulse.alerts")
settings = get_settings()


class AlertService:
    """Processes alerts based on monitor check results."""

    @staticmethod
    async def process_down_alert(
        db: AsyncSession,
        monitor: Monitor,
        check: MonitorCheck,
    ) -> None:
        """Process alert when monitor goes down."""
        if monitor.consecutive_failures < 2:
            return  # Wait for consecutive failures

        # Get active alert rules for this monitor
        rules_result = await db.execute(
            select(AlertRule).where(
                AlertRule.monitor_id == monitor.id,
                AlertRule.is_enabled == True,
                AlertRule.trigger_on_down == True,
            )
        )
        rules = rules_result.scalars().all()

        for rule in rules:
            # Check consecutive failures threshold
            if monitor.consecutive_failures < rule.consecutive_failures:
                continue

            # Check response time threshold
            if rule.response_time_threshold_ms and check.response_time_ms < rule.response_time_threshold_ms:
                continue

            # Check cooldown
            if rule.last_triggered_at:
                cooldown_end = rule.last_triggered_at + timedelta(minutes=rule.cooldown_minutes)
                if datetime.utcnow() < cooldown_end:
                    continue

            # Check rate limit (alerts per hour)
            hour_ago = datetime.utcnow() - timedelta(hours=1)
            recent_alerts = await db.execute(
                select(Alert).where(
                    Alert.rule_id == rule.id,
                    Alert.fired_at >= hour_ago,
                )
            )
            if len(recent_alerts.scalars().all()) >= rule.max_alerts_per_hour:
                continue

            # Create and send alert
            await AlertService._fire_alert(db, rule, monitor, check)

    @staticmethod
    async def process_recovery_alert(
        db: AsyncSession,
        monitor: Monitor,
        check: MonitorCheck,
    ) -> None:
        """Process recovery alert when monitor comes back up."""
        if monitor.consecutive_failures > 0:
            return  # Not actually recovered

        rules_result = await db.execute(
            select(AlertRule).where(
                AlertRule.monitor_id == monitor.id,
                AlertRule.is_enabled == True,
                AlertRule.trigger_on_up == True,
            )
        )
        rules = rules_result.scalars().all()

        for rule in rules:
            await AlertService._fire_alert(
                db, rule, monitor, check, is_recovery=True
            )

    @staticmethod
    async def _fire_alert(
        db: AsyncSession,
        rule: AlertRule,
        monitor: Monitor,
        check: MonitorCheck,
        is_recovery: bool = False,
    ) -> Optional[Alert]:
        channel = await db.get(AlertChannel, rule.channel_id)
        if not channel or not channel.is_enabled:
            return None

        severity = AlertSeverity.RECOVERY if is_recovery else rule.severity
        status_text = "RECOVERED" if is_recovery else "DOWN"
        title = f"[{status_text}] {monitor.name}"
        message = (
            f"Monitor {monitor.name} ({monitor.url}) is {status_text}.\n"
            f"Response time: {check.response_time_ms}ms\n"
            f"Status code: {check.status_code}\n"
        )
        if check.error_message:
            message += f"Error: {check.error_message}\n"
        if is_recovery:
            message += f"Monitor recovered after {monitor.consecutive_failures} consecutive failures."

        alert = Alert(
            rule_id=rule.id,
            severity=severity,
            title=title,
            message=message,
            status="firing",
        )
        db.add(alert)

        # Send notification
        notifier = NotificationService()
        try:
            await notifier.send(channel, title, message)
            alert.status = "sent"
            alert.sent_via = channel.channel_type
            alert.sent_to = channel.email_address or channel.webhook_url or channel.chat_id
            alert.sent_at = datetime.utcnow()
        except Exception as e:
            logger.error(f"Failed to send alert via {channel.channel_type}: {e}")
            alert.status = "failed"
            alert.delivery_error = str(e)

        rule.last_triggered_at = datetime.utcnow()
        await db.flush()
        return alert

    @staticmethod
    async def acknowledge_alert(
        db: AsyncSession,
        alert_id: str,
        user_id: str,
    ) -> Optional[Alert]:
        alert = await db.get(Alert, alert_id)
        if not alert:
            return None
        alert.status = "acknowledged"
        alert.acknowledged_by = user_id
        alert.acknowledged_at = datetime.utcnow()
        await db.flush()
        return alert

    @staticmethod
    async def resolve_alert(db: AsyncSession, alert_id: str) -> Optional[Alert]:
        alert = await db.get(Alert, alert_id)
        if not alert:
            return None
        alert.status = "resolved"
        alert.resolved_at = datetime.utcnow()
        await db.flush()
        return alert

    @staticmethod
    async def get_monitor_alerts(
        db: AsyncSession,
        monitor_id: str,
        limit: int = 100,
    ) -> List[Alert]:
        result = await db.execute(
            select(Alert)
            .join(AlertRule)
            .where(AlertRule.monitor_id == monitor_id)
            .order_by(Alert.fired_at.desc())
            .limit(limit)
        )
        return result.scalars().all()
