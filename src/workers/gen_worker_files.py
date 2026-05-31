#!/usr/bin/env python3
"""Generate remaining Pulse platform files."""
import os

BASE = "/root/.hermes/workspace/night_projects/projects/2026-05-31-pulse"
def w(path, content):
    full = os.path.join(BASE, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)

# ═════════════════════════════════════════════════════════════════════════════
# WORKERS — Check worker
# ═════════════════════════════════════════════════════════════════════════════
w("src/workers/check_worker.py", '''
"""Monitor check worker — performs HTTP/TCP/ICMP checks."""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import httpx

from src.config.settings import get_settings
from src.models.monitor import Monitor, MonitorType, MonitorStatus

logger = logging.getLogger("pulse.worker")
settings = get_settings()


@dataclass
class CheckResult:
    """Result of a single monitor check."""
    is_up: bool
    status_code: Optional[int] = None
    response_time_ms: float = 0.0
    error_message: Optional[str] = None
    dns_resolution_ms: Optional[float] = None
    tls_handshake_ms: Optional[float] = None
    ttfb_ms: Optional[float] = None
    content_length: Optional[int] = None
    headers: Optional[dict] = None


@dataclass
class SSLInfo:
    """SSL certificate information."""
    issuer: str = ""
    subject: str = ""
    expires_at: Optional[str] = None
    days_remaining: int = 0
    is_valid: bool = True
    protocol: str = ""
    cipher: str = ""


class HTTPChecker:
    """Performs HTTP/HTTPS checks."""

    async def check(self, monitor: Monitor) -> CheckResult:
        """Execute an HTTP check against the monitor target."""
        url = monitor.url
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        timeout = httpx.Timeout(monitor.timeout_seconds, connect=5.0)
        start = time.monotonic()

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=monitor.follow_redirects,
                verify=monitor.verify_ssl,
            ) as client:
                response = await client.request(
                    method=monitor.method,
                    url=url,
                    headers=self._parse_headers(monitor.headers),
                    content=monitor.body,
                )

            elapsed = (time.monotonic() - start) * 1000

            # Check expected status code
            status_ok = True
            if monitor.expected_status_code:
                status_ok = response.status_code == monitor.expected_status_code
            else:
                status_ok = response.status_code < 400

            # Check expected keyword
            keyword_ok = True
            if monitor.expected_keyword:
                keyword_ok = monitor.expected_keyword in response.text

            is_up = status_ok and keyword_ok
            error = None
            if not status_ok:
                error = f"Expected status {monitor.expected_status_code}, got {response.status_code}"
            elif not keyword_ok:
                error = f"Expected keyword not found: {monitor.expected_keyword}"

            return CheckResult(
                is_up=is_up,
                status_code=response.status_code,
                response_time_ms=round(elapsed, 2),
                error_message=error,
                content_length=len(response.content),
                headers=dict(response.headers),
            )

        except httpx.TimeoutException:
            elapsed = (time.monotonic() - start) * 1000
            return CheckResult(
                is_up=False,
                response_time_ms=round(elapsed, 2),
                error_message=f"Request timed out after {monitor.timeout_seconds}s",
            )
        except httpx.ConnectError as e:
            elapsed = (time.monotonic() - start) * 1000
            return CheckResult(
                is_up=False,
                response_time_ms=round(elapsed, 2),
                error_message=f"Connection failed: {e}",
            )
        except ssl.SSLCertVerificationError as e:
            elapsed = (time.monotonic() - start) * 1000
            return CheckResult(
                is_up=False,
                response_time_ms=round(elapsed, 2),
                error_message=f"SSL certificate error: {e}",
            )
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return CheckResult(
                is_up=False,
                response_time_ms=round(elapsed, 2),
                error_message=f"Unexpected error: {type(e).__name__}: {e}",
            )

    @staticmethod
    def _parse_headers(headers_str: Optional[str]) -> dict:
        """Parse JSON headers string into dict."""
        if not headers_str:
            return {}
        import json
        try:
            return json.loads(headers_str)
        except (json.JSONDecodeError, TypeError):
            return {}


class TCPChecker:
    """Performs TCP port checks."""

    async def check(self, monitor: Monitor) -> CheckResult:
        parsed = urlparse(monitor.url)
        host = parsed.hostname or monitor.url.replace("tcp://", "").split(":")[0]
        port = parsed.port or monitor.port or 80
        timeout = monitor.timeout_seconds
        start = time.monotonic()

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
            elapsed = (time.monotonic() - start) * 1000
            writer.close()
            await writer.wait_closed()
            return CheckResult(
                is_up=True,
                response_time_ms=round(elapsed, 2),
            )
        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - start) * 1000
            return CheckResult(
                is_up=False,
                response_time_ms=round(elapsed, 2),
                error_message=f"TCP connection timed out after {timeout}s",
            )
        except ConnectionRefusedError:
            elapsed = (time.monotonic() - start) * 1000
            return CheckResult(
                is_up=False,
                response_time_ms=round(elapsed, 2),
                error_message="Connection refused",
            )
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return CheckResult(
                is_up=False,
                response_time_ms=round(elapsed, 2),
                error_message=f"TCP check error: {e}",
            )


class ICMPChecker:
    """Performs ICMP ping checks."""

    async def check(self, monitor: Monitor) -> CheckResult:
        import shutil
        host = monitor.url.replace("icmp://", "").split(":")[0]
        timeout = monitor.timeout_seconds
        start = time.monotonic()

        ping_cmd = shutil.which("ping")
        if not ping_cmd:
            return CheckResult(is_up=False, error_message="ping command not found")

        try:
            proc = await asyncio.create_subprocess_exec(
                ping_cmd, "-c", "1", "-W", str(timeout), host,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
            elapsed = (time.monotonic() - start) * 1000

            if proc.returncode == 0:
                return CheckResult(is_up=True, response_time_ms=round(elapsed, 2))
            else:
                return CheckResult(
                    is_up=False,
                    response_time_ms=round(elapsed, 2),
                    error_message=stderr.decode().strip() or "Ping failed",
                )
        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - start) * 1000
            return CheckResult(is_up=False, response_time_ms=round(elapsed, 2), error_message="Ping timed out")
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return CheckResult(is_up=False, response_time_ms=round(elapsed, 2), error_message=str(e))


class DNSChecker:
    """Performs DNS resolution checks."""

    async def check(self, monitor: Monitor) -> CheckResult:
        import socket
        host = monitor.url.replace("dns://", "").split(":")[0]
        start = time.monotonic()

        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, socket.gethostbyname, host),
                timeout=monitor.timeout_seconds,
            )
            elapsed = (time.monotonic() - start) * 1000
            return CheckResult(is_up=True, response_time_ms=round(elapsed, 2))
        except socket.gaierror as e:
            elapsed = (time.monotonic() - start) * 1000
            return CheckResult(is_up=False, response_time_ms=round(elapsed, 2), error_message=f"DNS resolution failed: {e}")
        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - start) * 1000
            return CheckResult(is_up=False, response_time_ms=round(elapsed, 2), error_message="DNS resolution timed out")
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return CheckResult(is_up=False, response_time_ms=round(elapsed, 2), error_message=str(e))


class CheckExecutor:
    """Routes monitor checks to appropriate checker."""

    def __init__(self):
        self.http_checker = HTTPChecker()
        self.tcp_checker = TCPChecker()
        self.icmp_checker = ICMPChecker()
        self.dns_checker = DNSChecker()

    async def execute(self, monitor: Monitor) -> CheckResult:
        """Execute check based on monitor type."""
        checker_map = {
            MonitorType.HTTP: self.http_checker,
            MonitorType.HTTPS: self.http_checker,
            MonitorType.TCP: self.tcp_checker,
            MonitorType.ICMP: self.icmp_checker,
            MonitorType.DNS: self.dns_checker,
            MonitorType.KEYWORD: self.http_checker,
            MonitorType.GRAPHQL: self.http_checker,
        }

        checker = checker_map.get(monitor.type)
        if not checker:
            return CheckResult(
                is_up=False,
                error_message=f"Unsupported monitor type: {monitor.type}",
            )

        return await checker.check(monitor)
''')

# ═════════════════════════════════════════════════════════════════════════════
# WORKERS — Scheduler
# ═════════════════════════════════════════════════════════════════════════════
w("src/workers/scheduler.py", '''"""APScheduler-based monitor check scheduler."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.config.database import async_session_factory
from src.models.monitor import Monitor, MonitorStatus
from src.workers.check_worker import CheckExecutor
from src.services.monitor_service import MonitorService
from src.services.alert_service import AlertService

logger = logging.getLogger("pulse.scheduler")
settings = get_settings()


class MonitorScheduler:
    """Schedules and runs monitor checks."""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            job_defaults={
                "coalesce": settings.scheduler_coalesce,
                "max_instances": settings.scheduler_max_instances,
            },
        )
        self.executor = CheckExecutor()
        self._running = False

    async def start(self):
        """Start the scheduler."""
        if not settings.scheduler_enabled:
            logger.warning("Scheduler is disabled in settings")
            return

        # Load all active monitors and schedule them
        async with async_session_factory() as session:
            monitors = await MonitorService.get_monitors_to_check(session)
            for monitor in monitors:
                self._schedule_monitor(monitor)
            logger.info(f"Scheduled {len(monitors)} monitors")

        self.scheduler.start()
        self._running = True
        logger.info("Scheduler started")

    async def stop(self):
        """Stop the scheduler."""
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False
            logger.info("Scheduler stopped")

    def _schedule_monitor(self, monitor: Monitor):
        """Add a monitor to the scheduler."""
        job_id = f"monitor_{monitor.id}"
        existing = self.scheduler.get_job(job_id)

        if existing:
            if not monitor.is_active or monitor.status == MonitorStatus.PAUSED:
                self.scheduler.remove_job(job_id)
                logger.info(f"Unscheduled monitor {monitor.name}")
                return
            # Reschedule if interval changed
            existing.reschedule(
                trigger=IntervalTrigger(seconds=monitor.interval_seconds)
            )
            return

        if not monitor.is_active or monitor.status == MonitorStatus.PAUSED:
            return

        self.scheduler.add_job(
            self._run_check,
            trigger=IntervalTrigger(seconds=monitor.interval_seconds),
            id=job_id,
            args=[monitor.id],
            name=f"Check: {monitor.name}",
            replace_existing=True,
        )
        logger.info(f"Scheduled monitor {monitor.name} (every {monitor.interval_seconds}s)")

    async def _run_check(self, monitor_id: str):
        """Execute a single monitor check and process results."""
        async with async_session_factory() as session:
            monitor = await session.get(Monitor, monitor_id)
            if not monitor or not monitor.is_active:
                return

            logger.debug(f"Running check for {monitor.name}")
            result = await self.executor.execute(monitor)

            # Record check result
            check = await MonitorService.record_check(
                db=session,
                monitor_id=monitor.id,
                is_up=result.is_up,
                response_time_ms=result.response_time_ms,
                status_code=result.status_code,
                error_message=result.error_message,
                dns_ms=result.dns_resolution_ms,
                tls_ms=result.tls_handshake_ms,
                ttfb_ms=result.ttfb_ms,
                content_length=result.content_length,
            )

            # Process alerts
            if not result.is_up:
                logger.warning(f"Monitor {monitor.name} is DOWN: {result.error_message}")
                await AlertService.process_down_alert(session, monitor, check)
            else:
                await AlertService.process_recovery_alert(session, monitor, check)

            await session.commit()

    def get_status(self) -> dict:
        """Get scheduler status."""
        jobs = self.scheduler.get_jobs()
        return {
            "running": self._running,
            "scheduled_jobs": len(jobs),
            "jobs": [
                {"id": j.id, "name": j.name, "next_run": str(j.next_run_time)}
                for j in jobs
            ],
        }
''')

# ═════════════════════════════════════════════════════════════════════════════
# SERVICES — Alert
# ═════════════════════════════════════════════════════════════════════════════
w("src/services/alert_service.py", '''"""Alert processing service."""

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
            f"Monitor {monitor.name} ({monitor.url}) is {status_text}.\\n"
            f"Response time: {check.response_time_ms}ms\\n"
            f"Status code: {check.status_code}\\n"
        )
        if check.error_message:
            message += f"Error: {check.error_message}\\n"
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
''')

# ═════════════════════════════════════════════════════════════════════════════
# SERVICES — Notification
# ═════════════════════════════════════════════════════════════════════════════
w("src/services/notification_service.py", '''"""Notification delivery service."""

from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

from src.config.settings import get_settings
from src.models.alert import AlertChannel, AlertChannelType

logger = logging.getLogger("pulse.notifications")
settings = get_settings()


class NotificationService:
    """Sends notifications through various channels."""

    async def send(self, channel: AlertChannel, title: str, message: str) -> bool:
        """Send notification through the specified channel."""
        dispatch = {
            AlertChannelType.EMAIL: self._send_email,
            AlertChannelType.SLACK: self._send_slack,
            AlertChannelType.DISCORD: self._send_discord,
            AlertChannelType.TELEGRAM: self._send_telegram,
            AlertChannelType.WEBHOOK: self._send_webhook,
        }

        handler = dispatch.get(channel.channel_type)
        if not handler:
            logger.error(f"Unknown channel type: {channel.channel_type}")
            return False

        return await handler(channel, title, message)

    async def _send_email(self, channel: AlertChannel, title: str, message: str) -> bool:
        """Send email notification."""
        if not channel.email_address or not settings.smtp_host:
            logger.warning("Email channel not configured")
            return False

        try:
            import aiosmtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart()
            msg["From"] = settings.smtp_from
            msg["To"] = channel.email_address
            msg["Subject"] = title

            body = f"""
            <html>
            <body>
                <h2>{title}</h2>
                <pre>{message}</pre>
                <hr>
                <p><small>Sent by Pulse Monitoring Platform</small></p>
            </body>
            </html>
            """
            msg.attach(MIMEText(body, "html"))

            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user or None,
                password=settings.smtp_password.get_secret_value() or None,
                use_tls=settings.smtp_port == 587,
            )
            logger.info(f"Email sent to {channel.email_address}")
            return True
        except ImportError:
            logger.error("aiosmtplib not installed, cannot send email")
            return False
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            raise

    async def _send_slack(self, channel: AlertChannel, title: str, message: str) -> bool:
        """Send Slack notification."""
        webhook_url = channel.webhook_url or settings.slack_webhook_url
        if not webhook_url:
            logger.warning("Slack webhook URL not configured")
            return False

        payload = {
            "text": title,
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": title},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"```{message}```"},
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"Sent by Pulse • {__import__('datetime').datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"}
                    ],
                },
            ],
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(webhook_url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("Slack notification sent")
                return True
            raise Exception(f"Slack returned {response.status_code}: {response.text}")

    async def _send_discord(self, channel: AlertChannel, title: str, message: str) -> bool:
        """Send Discord notification."""
        webhook_url = channel.webhook_url
        if not webhook_url:
            logger.warning("Discord webhook URL not configured")
            return False

        payload = {
            "embeds": [
                {
                    "title": title,
                    "description": message[:4000],
                    "color": 15158332 if "DOWN" in title else 3066993,
                    "timestamp": __import__('datetime').datetime.utcnow().isoformat(),
                    "footer": {"text": "Pulse Monitoring"},
                }
            ]
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(webhook_url, json=payload, timeout=10)
            if response.status_code in (200, 204):
                logger.info("Discord notification sent")
                return True
            raise Exception(f"Discord returned {response.status_code}: {response.text}")

    async def _send_telegram(self, channel: AlertChannel, title: str, message: str) -> bool:
        """Send Telegram notification."""
        bot_token = settings.telegram_bot_token
        chat_id = channel.chat_id or settings.telegram_default_chat_id
        if not bot_token or not chat_id:
            logger.warning("Telegram not configured")
            return False

        text = f"*{title}*\\n\\n{message}"
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": "Markdown",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("Telegram notification sent")
                return True
            raise Exception(f"Telegram returned {response.status_code}: {response.text}")

    async def _send_webhook(self, channel: AlertChannel, title: str, message: str) -> bool:
        """Send webhook notification."""
        webhook_url = channel.webhook_url
        if not webhook_url:
            logger.warning("Webhook URL not configured")
            return False

        payload = {
            "source": "pulse",
            "title": title,
            "message": message,
            "channel": channel.name,
            "timestamp": __import__('datetime').datetime.utcnow().isoformat(),
            "config": channel.config or {},
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if response.status_code < 400:
                logger.info(f"Webhook sent to {webhook_url}")
                return True
            raise Exception(f"Webhook returned {response.status_code}")
''')

# ═════════════════════════════════════════════════════════════════════════════
# SERVICES — Dashboard
# ═════════════════════════════════════════════════════════════════════════════
w("src/services/dashboard_service.py", '''"""Dashboard and widget management service."""

from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.dashboard import Dashboard, DashboardWidget, WidgetType


class DashboardService:
    """CRUD operations for dashboards and widgets."""

    @staticmethod
    async def create_dashboard(
        db: AsyncSession,
        user_id: str,
        name: str,
        slug: str,
        description: Optional[str] = None,
        columns: int = 3,
        theme: str = "dark",
        team_id: Optional[str] = None,
    ) -> Dashboard:
        dashboard = Dashboard(
            user_id=user_id,
            team_id=team_id,
            name=name,
            slug=slug,
            description=description,
            columns=columns,
            theme=theme,
        )
        db.add(dashboard)
        await db.flush()
        return dashboard

    @staticmethod
    async def get_user_dashboards(
        db: AsyncSession,
        user_id: str,
    ) -> List[Dashboard]:
        result = await db.execute(
            select(Dashboard).where(Dashboard.user_id == user_id).order_by(Dashboard.created_at.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def get_dashboard(
        db: AsyncSession,
        dashboard_id: str,
        user_id: str,
    ) -> Optional[Dashboard]:
        result = await db.execute(
            select(Dashboard).where(Dashboard.id == dashboard_id, Dashboard.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_dashboard_by_slug(
        db: AsyncSession,
        slug: str,
        user_id: str,
    ) -> Optional[Dashboard]:
        result = await db.execute(
            select(Dashboard).where(Dashboard.slug == slug, Dashboard.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_dashboard(db: AsyncSession, dashboard: Dashboard, **kwargs) -> Dashboard:
        allowed = {"name", "slug", "description", "columns", "theme", "is_public", "public_slug", "refresh_interval_seconds"}
        for key, value in kwargs.items():
            if key in allowed and value is not None:
                setattr(dashboard, key, value)
        await db.flush()
        return dashboard

    @staticmethod
    async def delete_dashboard(db: AsyncSession, dashboard: Dashboard) -> None:
        await db.delete(dashboard)
        await db.flush()

    # ── Widgets ───────────────────────────────────────────────────────────

    @staticmethod
    async def add_widget(
        db: AsyncSession,
        dashboard_id: str,
        name: str,
        widget_type: str,
        position: int = 0,
        width: int = 1,
        height: int = 1,
        config: Optional[dict] = None,
        monitor_ids: Optional[list] = None,
    ) -> DashboardWidget:
        widget = DashboardWidget(
            dashboard_id=dashboard_id,
            name=name,
            widget_type=widget_type,
            position=position,
            width=width,
            height=height,
            config=config,
            monitor_ids=monitor_ids,
        )
        db.add(widget)
        await db.flush()
        return widget

    @staticmethod
    async def update_widget(db: AsyncSession, widget: DashboardWidget, **kwargs) -> DashboardWidget:
        allowed = {"name", "widget_type", "position", "width", "height", "config", "monitor_ids"}
        for key, value in kwargs.items():
            if key in allowed and value is not None:
                setattr(widget, key, value)
        await db.flush()
        return widget

    @staticmethod
    async def delete_widget(db: AsyncSession, widget: DashboardWidget) -> None:
        await db.delete(widget)
        await db.flush()

    @staticmethod
    async def get_dashboard_with_data(
        db: AsyncSession,
        dashboard_id: str,
        user_id: str,
    ) -> Optional[dict]:
        """Get dashboard with all widget data populated."""
        dashboard = await DashboardService.get_dashboard(db, dashboard_id, user_id)
        if not dashboard:
            return None

        widgets_data = []
        for widget in dashboard.widgets:
            widget_data = {
                "id": widget.id,
                "name": widget.name,
                "type": widget.widget_type,
                "position": widget.position,
                "width": widget.width,
                "height": widget.height,
                "config": widget.config,
                "data": await DashboardService._get_widget_data(db, widget),
            }
            widgets_data.append(widget_data)

        return {
            "id": dashboard.id,
            "name": dashboard.name,
            "slug": dashboard.slug,
            "description": dashboard.description,
            "columns": dashboard.columns,
            "theme": dashboard.theme,
            "widgets": widgets_data,
        }

    @staticmethod
    async def _get_widget_data(db: AsyncSession, widget: DashboardWidget) -> dict:
        """Get data for a specific widget type."""
        from src.models.monitor import Monitor, MonitorCheck, MonitorStatus

        monitor_ids = widget.monitor_ids

        if widget.widget_type == WidgetType.STATUS_PIECE:
            if monitor_ids:
                monitors = []
                for mid in monitor_ids:
                    m = await db.get(Monitor, mid)
                    if m:
                        monitors.append(m)
            else:
                result = await db.execute(select(Monitor).where(Monitor.is_active == True).limit(1))
                monitors = result.scalars().all()

            if monitors:
                return {
                    "monitor": {"id": monitors[0].id, "name": monitors[0].name},
                    "status": monitors[0].status,
                    "uptime": monitors[0].uptime_percentage,
                    "response_time": monitors[0].avg_response_time_ms,
                }
            return {"status": "unknown"}

        elif widget.widget_type == WidgetType.STATS_OVERVIEW:
            total_result = await db.execute(select(func.count()).select_from(Monitor))
            up_result = await db.execute(
                select(func.count()).select_from(Monitor).where(Monitor.status == MonitorStatus.UP)
            )
            down_result = await db.execute(
                select(func.count()).select_from(Monitor).where(Monitor.status == MonitorStatus.DOWN)
            )
            total = total_result.scalar_one()
            up = up_result.scalar_one()
            down = down_result.scalar_one()
            return {"total": total, "up": up, "down": down, "uptime": round((up / total) * 100, 1) if total else 100}

        elif widget.widget_type == WidgetType.UPTIME_BAR:
            if monitor_ids:
                checks = []
                for mid in monitor_ids[:1]:
                    from datetime import datetime, timedelta
                    since = datetime.utcnow() - timedelta(days=30)
                    result = await db.execute(
                        select(MonitorCheck).where(
                            MonitorCheck.monitor_id == mid,
                            MonitorCheck.checked_at >= since,
                        ).order_by(MonitorCheck.checked_at.desc()).limit(90)
                    )
                    checks = result.scalars().all()
            else:
                checks = []

            return {
                "bars": [
                    {"date": c.checked_at.isoformat(), "up": c.is_up, "response_ms": c.response_time_ms}
                    for c in reversed(checks)
                ]
            }

        elif widget.widget_type == WidgetType.MONITOR_LIST:
            if monitor_ids:
                monitors = []
                for mid in monitor_ids:
                    m = await db.get(Monitor, mid)
                    if m:
                        monitors.append(m)
            else:
                result = await db.execute(
                    select(Monitor).where(Monitor.is_active == True).order_by(Monitor.name).limit(50)
                )
                monitors = result.scalars().all()

            return {
                "monitors": [
                    {
                        "id": m.id, "name": m.name, "status": m.status,
                        "uptime": m.uptime_percentage, "response_time": m.avg_response_time_ms,
                        "url": m.url, "type": m.type,
                    }
                    for m in monitors
                ]
            }

        return {}
''')

# ═════════════════════════════════════════════════════════════════════════════
# SERVICES — Incident
# ═════════════════════════════════════════════════════════════════════════════
w("src/services/incident_service.py", '''"""Incident management service."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.incident import Incident, IncidentEvent, IncidentStatus, IncidentSeverity


class IncidentService:
    """CRUD and management of incidents."""

    @staticmethod
    async def create_incident(
        db: AsyncSession,
        monitor_id: str,
        title: str,
        description: Optional[str] = None,
        severity: str = IncidentSeverity.MAJOR,
        created_by: Optional[str] = None,
    ) -> Incident:
        now = datetime.utcnow()
        incident = Incident(
            monitor_id=monitor_id,
            title=title,
            description=description,
            severity=severity,
            status=IncidentStatus.INVESTIGATING,
            started_at=now,
            created_by=created_by,
        )
        db.add(incident)

        # Add creation event
        event = IncidentEvent(
            incident_id=incident.id,
            user_id=created_by,
            event_type="created",
            message=f"Investigating: {title}",
            new_status=IncidentStatus.INVESTIGATING,
        )
        db.add(event)
        await db.flush()
        return incident

    @staticmethod
    async def update_status(
        db: AsyncSession,
        incident_id: str,
        new_status: str,
        user_id: Optional[str] = None,
        message: Optional[str] = None,
    ) -> Optional[Incident]:
        incident = await db.get(Incident, incident_id)
        if not incident:
            return None

        old_status = incident.status
        incident.status = new_status

        if new_status == IncidentStatus.RESOLVED:
            incident.resolved_at = datetime.utcnow()
            incident.duration_seconds = (incident.resolved_at - incident.started_at).total_seconds()
        elif new_status == IncidentStatus.IDENTIFIED:
            incident.identified_at = datetime.utcnow()

        event = IncidentEvent(
            incident_id=incident.id,
            user_id=user_id,
            event_type="status_change",
            message=message or f"Status changed from {old_status} to {new_status}",
            old_status=old_status,
            new_status=new_status,
        )
        db.add(event)
        await db.flush()
        return incident

    @staticmethod
    async def add_comment(
        db: AsyncSession,
        incident_id: str,
        user_id: str,
        message: str,
    ) -> Optional[IncidentEvent]:
        incident = await db.get(Incident, incident_id)
        if not incident:
            return None

        event = IncidentEvent(
            incident_id=incident.id,
            user_id=user_id,
            event_type="comment",
            message=message,
        )
        db.add(event)
        await db.flush()
        return event

    @staticmethod
    async def resolve_incident(
        db: AsyncSession,
        incident_id: str,
        user_id: Optional[str] = None,
        resolution_notes: Optional[str] = None,
    ) -> Optional[Incident]:
        incident = await db.update_status(
            db, incident_id, IncidentStatus.RESOLVED, user_id,
            message=resolution_notes or "Incident resolved",
        )
        if incident and resolution_notes:
            incident.resolution_notes = resolution_notes
        await db.flush()
        return incident

    @staticmethod
    async def get_active_incidents(
        db: AsyncSession,
        monitor_id: Optional[str] = None,
    ) -> List[Incident]:
        query = select(Incident).where(Incident.status != IncidentStatus.RESOLVED)
        if monitor_id:
            query = query.where(Incident.monitor_id == monitor_id)
        query = query.order_by(Incident.started_at.desc())
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def get_incident_history(
        db: AsyncSession,
        monitor_id: str,
        limit: int = 50,
    ) -> List[Incident]:
        result = await db.execute(
            select(Incident)
            .where(Incident.monitor_id == monitor_id)
            .order_by(Incident.started_at.desc())
            .limit(limit)
        )
        return result.scalars().all()
''')

print("Workers and services written")
