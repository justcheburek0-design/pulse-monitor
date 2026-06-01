"""Pulse notification service.

Multi-channel notification delivery:
  - Email (SMTP via aiosmtplib)
  - Slack (webhook)
  - Discord (webhook)
  - Telegram (bot API)
  - Generic webhook (JSON POST, optional HMAC signing)
  - PagerDuty (Events API v2)

Features:
  - Exponential backoff retry
  - Async batch queue
  - Per-channel rate limiting
  - HTML email templates with Jinja2
  - Delivery logging and status tracking
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx
from src.config.settings import get_settings
from src.models.alert import Alert, AlertChannel, AlertChannelType, AlertSeverity

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Notification Delivery Status ──────────────────────────────────────────────

class DeliveryStatus(Enum):
    PENDING = "pending"
    SENT = "sent"
    RETRY = "retry"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"


@dataclass
class DeliveryResult:
    channel_id: str
    channel_type: str
    status: DeliveryStatus
    attempt: int
    max_attempts: int
    latency_ms: float = 0.0
    error: Optional[str] = None
    sent_at: Optional[datetime] = None
    response_code: Optional[int] = None

    @property
    def is_success(self) -> bool:
        return self.status == DeliveryStatus.SENT

    @property
    def will_retry(self) -> bool:
        return self.status == DeliveryStatus.RETRY and self.attempt < self.max_attempts


@dataclass
class NotificationPayload:
    """Unified notification payload across all channels."""
    title: str
    body: str
    severity: str
    monitor_name: str
    monitor_url: str
    status: str  # "up", "down", "warning"
    response_time_ms: float = 0.0
    uptime_pct: float = 100.0
    incident_id: Optional[str] = None
    alert_id: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_alert(cls, alert: Alert) -> NotificationPayload:
        """Create payload from an Alert ORM object."""
        monitor_name = alert.alert_rule.monitor.name if alert.alert_rule and alert.alert_rule.monitor else "Unknown"
        monitor_url = alert.alert_rule.monitor.url if alert.alert_rule and alert.alert_rule.monitor else ""
        return cls(
            title=f"[{alert.severity.upper()}] {monitor_name} is {alert.status}",
            body=alert.message or f"Monitor {monitor_name} ({monitor_url}) is {alert.status}",
            severity=alert.severity,
            monitor_name=monitor_name,
            monitor_url=monitor_url,
            status=alert.status,
            response_time_ms=alert.alert_rule.monitor.avg_response_time_ms if alert.alert_rule and alert.alert_rule.monitor else 0.0,
            uptime_pct=alert.alert_rule.monitor.uptime_percentage if alert.alert_rule and alert.alert_rule.monitor else 100.0,
            incident_id=alert.incident_id,
            alert_id=alert.id,
        )


# ── Email Template Engine ─────────────────────────────────────────────────────

EMAIL_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
  .container { max-width: 600px; margin: 0 auto; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
  .header { padding: 24px; color: #fff; background: {{ header_color }}; }
  .header h1 { margin: 0; font-size: 20px; }
  .body { padding: 24px; }
  .metric { display: inline-block; margin: 8px 16px 8px 0; padding: 8px 12px; background: #f0f0f0; border-radius: 4px; }
  .metric-label { font-size: 12px; color: #666; }
  .metric-value { font-size: 18px; font-weight: bold; color: #333; }
  .footer { padding: 16px 24px; background: #fafafa; font-size: 12px; color: #999; }
  .btn { display: inline-block; padding: 10px 20px; background: #4F46E5; color: #fff !important; text-decoration: none; border-radius: 4px; margin: 8px 4px; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{{ title }}</h1>
  </div>
  <div class="body">
    <p>{{ body }}</p>
    <div>
      <div class="metric"><div class="metric-label">Monitor</div><div class="metric-value">{{ monitor_name }}</div></div>
      <div class="metric"><div class="metric-label">URL</div><div class="metric-value"><a href="{{ monitor_url }}">{{ monitor_url }}</a></div></div>
      <div class="metric"><div class="metric-label">Response Time</div><div class="metric-value">{{ "%.0f"|format(response_time_ms) }} ms</div></div>
      <div class="metric"><div class="metric-label">Uptime</div><div class="metric-value">{{ "%.2f"|format(uptime_pct) }}%</div></div>
    </div>
    <div style="margin-top: 16px;">
      <a href="{{ dashboard_url }}" class="btn">Open Dashboard</a>
      {% if incident_id %}<a href="{{ incident_url }}" class="btn">View Incident</a>{% endif %}
    </div>
  </div>
  <div class="footer">
    Pulse Monitoring · {{ timestamp }} · <a href="{{ unsubscribe_url }}">Unsubscribe</a>
  </div>
</div>
</body>
</html>
"""

EMAIL_TEXT_TEMPLATE = """
{{ title }}
{{ "=" * title|length }}

{{ body }}

Monitor:    {{ monitor_name }}
URL:        {{ monitor_url }}
Status:     {{ status }}
Response:   {{ "%.0f"|format(response_time_ms) }} ms
Uptime:     {{ "%.2f"|format(uptime_pct) }}%

Dashboard:  {{ dashboard_url }}
{% if incident_id %}Incident:   {{ incident_url }}{% endif %}

---
Pulse Monitoring · {{ timestamp }}
Unsubscribe: {{ unsubscribe_url }}
"""


def _severity_color(severity: str) -> str:
    colors = {
        "critical": "#DC2626",
        "major": "#EA580C",
        "minor": "#D97706",
        "warning": "#F59E0B",
        "info": "#3B82F6",
        "recovery": "#16A34A",
    }
    return colors.get(severity.lower(), "#374151")


def render_email(payload: NotificationPayload) -> tuple:
    """Render HTML and plain text email content from payload."""
    try:
        from jinja2 import Template
        template_vars = {
            "title": payload.title,
            "body": payload.body,
            "monitor_name": payload.monitor_name,
            "monitor_url": payload.monitor_url,
            "status": payload.status,
            "response_time_ms": payload.response_time_ms,
            "uptime_pct": payload.uptime_pct,
            "severity": payload.severity,
            "timestamp": payload.timestamp.strftime("%Y-%m-%d %H:%M UTC"),
            "dashboard_url": f"{settings.app_url}/dashboard",
            "incident_url": f"{settings.app_url}/incidents/{payload.incident_id}" if payload.incident_id else "",
            "unsubscribe_url": f"{settings.app_url}/unsubscribe",
            "incident_id": payload.incident_id,
            "header_color": _severity_color(payload.severity),
        }
        html = Template(EMAIL_HTML_TEMPLATE).render(**template_vars)
        text = Template(EMAIL_TEXT_TEMPLATE).render(**template_vars)
        return html, text
    except ImportError:
        # Fallback if jinja2 not installed
        return f"<pre>{payload.body}</pre>", payload.body


# ── Sender implementations ────────────────────────────────────────────────────

class EmailSender:
    """Send emails via SMTP using aiosmtplib."""

    async def send(self, to_email: str, payload: NotificationPayload) -> DeliveryResult:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        html_body, text_body = render_email(payload)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = payload.title
        msg["From"] = f"Pulse <{settings.smtp_user}>"
        msg["To"] = to_email
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        start = time.monotonic()
        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                use_tls=settings.smtp_use_tls,
                username=settings.smtp_user,
                password=settings.smtp_password,
                timeout=15,
            )
            latency = (time.monotonic() - start) * 1000
            return DeliveryResult(
                channel_id="email", channel_type="email",
                status=DeliveryStatus.SENT, attempt=1, max_attempts=1,
                latency_ms=latency, sent_at=datetime.utcnow(),
            )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            return DeliveryResult(
                channel_id="email", channel_type="email",
                status=DeliveryStatus.FAILED, attempt=1, max_attempts=1,
                latency_ms=latency, error=str(e),
            )


class SlackSender:
    """Send notifications to Slack via Incoming Webhook."""

    async def send(self, webhook_url: str, payload: NotificationPayload) -> DeliveryResult:
        color_map = {
            "critical": "#FF0000",
            "major": "#FF8C00",
            "minor": "#FFD700",
            "warning": "#FFA500",
            "info": "#4A90D9",
            "recovery": "#36A64F",
        }
        slack_payload = {
            "attachments": [{
                "color": color_map.get(payload.severity, "#808080"),
                "title": payload.title,
                "text": payload.body,
                "fields": [
                    {"title": "Monitor", "value": payload.monitor_name, "short": True},
                    {"title": "Status", "value": payload.status.upper(), "short": True},
                    {"title": "URL", "value": payload.monitor_url, "short": False},
                    {"title": "Response Time", "value": f'{payload.response_time_ms:.0f} ms', "short": True},
                    {"title": "Uptime", "value": f'{payload.uptime_pct:.2f}%', "short": True},
                ],
                "ts": int(payload.timestamp.timestamp()),
                "footer": "Pulse Monitoring",
            }]
        }
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(webhook_url, json=slack_payload)
            latency = (time.monotonic() - start) * 1000
            return DeliveryResult(
                channel_id="slack", channel_type="slack",
                status=DeliveryStatus.SENT if resp.status_code == 200 else DeliveryStatus.FAILED,
                attempt=1, max_attempts=1, latency_ms=latency,
                sent_at=datetime.utcnow(), response_code=resp.status_code,
                error=None if resp.status_code == 200 else resp.text[:200],
            )
        except Exception as e:
            return DeliveryResult(
                channel_id="slack", channel_type="slack",
                status=DeliveryStatus.FAILED, attempt=1, max_attempts=1,
                latency_ms=(time.monotonic() - start) * 1000, error=str(e),
            )


class DiscordSender:
    """Send notifications to Discord via Webhook."""

    async def send(self, webhook_url: str, payload: NotificationPayload) -> DeliveryResult:
        color_map = {
            "critical": 15158332,
            "major": 15105570,
            "minor": 15844367,
            "warning": 16776960,
            "info": 3447003,
            "recovery": 3066993,
        }
        discord_payload = {
            "embeds": [{
                "title": payload.title,
                "description": payload.body,
                "color": color_map.get(payload.severity, 8421504),
                "fields": [
                    {"name": "Monitor", "value": payload.monitor_name, "inline": True},
                    {"name": "Status", "value": payload.status.upper(), "inline": True},
                    {"name": "URL", "value": f"[Open]({payload.monitor_url})", "inline": True},
                    {"name": "Response", "value": f"{payload.response_time_ms:.0f} ms", "inline": True},
                    {"name": "Uptime", "value": f"{payload.uptime_pct:.2f}%", "inline": True},
                ],
                "timestamp": payload.timestamp.isoformat(),
                "footer": {"text": "Pulse Monitoring"},
            }]
        }
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(webhook_url, json=discord_payload)
            latency = (time.monotonic() - start) * 1000
            return DeliveryResult(
                channel_id="discord", channel_type="discord",
                status=DeliveryStatus.SENT if resp.status_code in (200, 204) else DeliveryStatus.FAILED,
                attempt=1, max_attempts=1, latency_ms=latency,
                sent_at=datetime.utcnow(), response_code=resp.status_code,
            )
        except Exception as e:
            return DeliveryResult(
                channel_id="discord", channel_type="discord",
                status=DeliveryStatus.FAILED, attempt=1, max_attempts=1,
                latency_ms=(time.monotonic() - start) * 1000, error=str(e),
            )


class TelegramSender:
    """Send notifications via Telegram Bot API."""

    async def send(self, bot_token: str, chat_id: str, payload: NotificationPayload) -> DeliveryResult:
        severity_emoji = {
            "critical": "🔴", "major": "🟠", "minor": "🟡",
            "warning": "⚠️", "info": "ℹ️", "recovery": "✅",
        }
        emoji = severity_emoji.get(payload.severity, "•")
        text = (
            f"{emoji} *{payload.title}*\n\n"
            f"{payload.body}\n\n"
            f"📡 Monitor: `{payload.monitor_name}`\n"
            f"🌐 URL: {payload.monitor_url}\n"
            f"📊 Status: *{payload.status.upper()}*\n"
            f"⏱ Response: {payload.response_time_ms:.0f} ms\n"
            f"📈 Uptime: {payload.uptime_pct:.2f}%\n"
        )
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        telegram_payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=telegram_payload)
            latency = (time.monotonic() - start) * 1000
            return DeliveryResult(
                channel_id="telegram", channel_type="telegram",
                status=DeliveryStatus.SENT if resp.status_code == 200 else DeliveryStatus.FAILED,
                attempt=1, max_attempts=1, latency_ms=latency,
                sent_at=datetime.utcnow(), response_code=resp.status_code,
            )
        except Exception as e:
            return DeliveryResult(
                channel_id="telegram", channel_type="telegram",
                status=DeliveryStatus.FAILED, attempt=1, max_attempts=1,
                latency_ms=(time.monotonic() - start) * 1000, error=str(e),
            )


class WebhookSender:
    """Send notifications via generic webhook with optional HMAC signing."""

    async def send(self, webhook_url: str, payload: NotificationPayload, secret: Optional[str] = None) -> DeliveryResult:
        data = {
            "event": "alert",
            "timestamp": payload.timestamp.isoformat(),
            "severity": payload.severity,
            "title": payload.title,
            "message": payload.body,
            "monitor": {
                "name": payload.monitor_name,
                "url": payload.monitor_url,
                "status": payload.status,
                "response_time_ms": payload.response_time_ms,
                "uptime_pct": payload.uptime_pct,
            },
            "incident_id": payload.incident_id,
            "alert_id": payload.alert_id,
            "metadata": payload.metadata,
        }
        headers = {"Content-Type": "application/json", "User-Agent": "Pulse-Webhook/1.0"}
        if secret:
            body_json = json.dumps(data)
            signature = hmac.new(
                secret.encode(), body_json.encode(), hashlib.sha256
            ).hexdigest()
            headers["X-Pulse-Signature"] = f"sha256={signature}"
        else:
            body_json = None

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    webhook_url,
                    json=data if body_json is None else None,
                    content=body_json,
                    headers=headers,
                )
            latency = (time.monotonic() - start) * 1000
            return DeliveryResult(
                channel_id="webhook", channel_type="webhook",
                status=DeliveryStatus.SENT if resp.status_code < 400 else DeliveryStatus.FAILED,
                attempt=1, max_attempts=1, latency_ms=latency,
                sent_at=datetime.utcnow(), response_code=resp.status_code,
            )
        except Exception as e:
            return DeliveryResult(
                channel_id="webhook", channel_type="webhook",
                status=DeliveryStatus.FAILED, attempt=1, max_attempts=1,
                latency_ms=(time.monotonic() - start) * 1000, error=str(e),
            )


class PagerDutySender:
    """Send events to PagerDuty Events API v2."""

    async def send(self, routing_key: str, payload: NotificationPayload) -> DeliveryResult:
        pd_payload = {
            "routing_key": routing_key,
            "event_action": "resolve" if payload.status == "up" else "trigger",
            "payload": {
                "summary": payload.title,
                "severity": _map_severity_to_pagerduty(payload.severity),
                "source": payload.monitor_url,
                "component": payload.monitor_name,
                "custom_details": {
                    "status": payload.status,
                    "response_time_ms": payload.response_time_ms,
                    "uptime_pct": payload.uptime_pct,
                },
            },
        }
        if payload.incident_id:
            pd_payload["dedup_key"] = payload.incident_id
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://events.pagerduty.com/v2/enqueue",
                    json=pd_payload,
                )
            latency = (time.monotonic() - start) * 1000
            return DeliveryResult(
                channel_id="pagerduty", channel_type="pagerduty",
                status=DeliveryStatus.SENT if resp.status_code == 202 else DeliveryStatus.FAILED,
                attempt=1, max_attempts=1, latency_ms=latency,
                sent_at=datetime.utcnow(), response_code=resp.status_code,
            )
        except Exception as e:
            return DeliveryResult(
                channel_id="pagerduty", channel_type="pagerduty",
                status=DeliveryStatus.FAILED, attempt=1, max_attempts=1,
                latency_ms=(time.monotonic() - start) * 1000, error=str(e),
            )


def _map_severity_to_pagerduty(severity: str) -> str:
    mapping = {
        "critical": "critical",
        "major": "error",
        "minor": "warning",
        "warning": "warning",
        "info": "info",
    }
    return mapping.get(severity.lower(), "warning")


# ── Rate Limiter ──────────────────────────────────────────────────────────────

class RateLimiter:
    """Simple token-bucket rate limiter for notification channels."""

    def __init__(self, rate: int = 60, period: int = 60):
        self.rate = rate
        self.period = period
        self._tokens = rate
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.rate, self._tokens + elapsed * (self.rate / self.period))
            self._last_refill = now
            if self._tokens >= 1:
                self._tokens -= 1
                return True
            return False

    async def wait_and_acquire(self):
        while not await self.acquire():
            await asyncio.sleep(0.1)


# ── Retry with Exponential Backoff ────────────────────────────────────────────

async def retry_with_backoff(
    fn,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
):
    """Call fn() with exponential backoff retry."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn(), attempt
        except Exception as e:
            last_error = e
            if attempt < max_attempts:
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                await asyncio.sleep(delay)
    raise last_error


# ── Notification Queue ────────────────────────────────────────────────────────

@dataclass
class QueuedNotification:
    channel_id: str
    channel_type: str
    channel_config: Dict[str, Any]
    payload: NotificationPayload
    queued_at: datetime = field(default_factory=datetime.utcnow)
    attempt: int = 0
    max_attempts: int = 3


class NotificationQueue:
    """Async batch queue for notification delivery."""

    def __init__(self, batch_size: int = 10, flush_interval: float = 5.0):
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._queue: asyncio.Queue[QueuedNotification] = asyncio.Queue()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._rate_limiters: Dict[str, RateLimiter] = {}
        self._delivery_log: List[DeliveryResult] = []

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._process_loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def enqueue(self, notification: QueuedNotification):
        await self._queue.put(notification)

    async def enqueue_many(self, notifications: List[QueuedNotification]):
        for n in notifications:
            await self._queue.put(n)

    def get_rate_limiter(self, channel_type: str) -> RateLimiter:
        if channel_type not in self._rate_limiters:
            self._rate_limiters[channel_type] = RateLimiter(rate=30, period=60)
        return self._rate_limiters[channel_type]

    async def _process_loop(self):
        while self._running:
            batch = []
            try:
                # Collect a batch
                while len(batch) < self.batch_size:
                    try:
                        item = self._queue.get_nowait()
                        batch.append(item)
                    except asyncio.QueueEmpty:
                        break

                if batch:
                    await self._deliver_batch(batch)
                else:
                    await asyncio.sleep(min(0.5, self.flush_interval))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Notification queue error: {e}")
                await asyncio.sleep(1)

    async def _deliver_batch(self, batch: List[QueuedNotification]):
        results = await asyncio.gather(
            *[self._deliver_single(n) for n in batch],
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, DeliveryResult):
                self._delivery_log.append(result)

    async def _deliver_single(self, n: QueuedNotification) -> DeliveryResult:
        limiter = self.get_rate_limiter(n.channel_type)
        await limiter.wait_and_acquire()

        sender_map = {
            "email": EmailSender(),
            "slack": SlackSender(),
            "discord": DiscordSender(),
            "telegram": TelegramSender(),
            "webhook": WebhookSender(),
            "pagerduty": PagerDutySender(),
        }
        sender = sender_map.get(n.channel_type)
        if not sender:
            return DeliveryResult(
                channel_id=n.channel_id, channel_type=n.channel_type,
                status=DeliveryStatus.FAILED, attempt=1, max_attempts=1,
                error=f"Unknown channel type: {n.channel_type}",
            )

        max_attempts = n.max_attempts
        config = n.channel_config
        last_result = None

        for attempt in range(1, max_attempts + 1):
            try:
                if n.channel_type == "email":
                    result = await sender.send(config.get("email", ""), n.payload)
                elif n.channel_type == "slack":
                    result = await sender.send(config.get("webhook_url", ""), n.payload)
                elif n.channel_type == "discord":
                    result = await sender.send(config.get("webhook_url", ""), n.payload)
                elif n.channel_type == "telegram":
                    result = await sender.send(
                        config.get("bot_token", ""), config.get("chat_id", ""), n.payload
                    )
                elif n.channel_type == "webhook":
                    result = await sender.send(
                        config.get("url", ""), n.payload,
                        secret=config.get("secret"),
                    )
                elif n.channel_type == "pagerduty":
                    result = await sender.send(config.get("routing_key", ""), n.payload)
                else:
                    result = DeliveryResult(
                        channel_id=n.channel_id, channel_type=n.channel_type,
                        status=DeliveryStatus.FAILED, attempt=attempt,
                        max_attempts=max_attempts, error="Unknown type",
                    )

                result.attempt = attempt
                attempt = attempt

                if result.is_success:
                    return result
                last_result = result
                if attempt < max_attempts:
                    delay = min(1.0 * (2 ** (attempt - 1)), 30.0)
                    await asyncio.sleep(delay)
            except Exception as e:
                last_result = DeliveryResult(
                    channel_id=n.channel_id, channel_type=n.channel_type,
                    status=DeliveryStatus.FAILED, attempt=attempt,
                    max_attempts=max_attempts, error=str(e),
                )
                if attempt < max_attempts:
                    await asyncio.sleep(1.0 * (2 ** (attempt - 1)))

        return last_result or DeliveryResult(
            channel_id=n.channel_id, channel_type=n.channel_type,
            status=DeliveryStatus.FAILED, attempt=max_attempts,
            max_attempts=max_attempts, error="All attempts failed",
        )

    def get_delivery_stats(self) -> Dict[str, Any]:
        """Get delivery statistics."""
        total = len(self._delivery_log)
        sent = sum(1 for r in self._delivery_log if r.is_success)
        failed = total - sent
        by_type: Dict[str, Dict[str, int]] = {}
        for r in self._delivery_log:
            if r.channel_type not in by_type:
                by_type[r.channel_type] = {"sent": 0, "failed": 0}
            if r.is_success:
                by_type[r.channel_type]["sent"] += 1
            else:
                by_type[r.channel_type]["failed"] += 1
        avg_latency = (
            sum(r.latency_ms for r in self._delivery_log if r.is_success) / sent
            if sent > 0 else 0
        )
        return {
            "total": total, "sent": sent, "failed": failed,
            "success_rate": round(sent / total * 100, 1) if total > 0 else 0,
            "avg_latency_ms": round(avg_latency, 1),
            "by_channel": by_type,
        }


# ── Main Notification Service ─────────────────────────────────────────────────

class NotificationService:
    """High-level notification service that dispatches alerts to channels."""

    def __init__(self):
        self.queue = NotificationQueue()
        self._started = False

    async def start(self):
        if not self._started:
            await self.queue.start()
            self._started = True

    async def stop(self):
        await self.queue.stop()
        self._started = False

    async def dispatch_alert(self, alert: Alert, channels: List[AlertChannel]) -> List[DeliveryResult]:
        """Dispatch an alert to all configured channels."""
        payload = NotificationPayload.from_alert(alert)
        notifications = []
        for ch in channels:
            notifications.append(QueuedNotification(
                channel_id=ch.id,
                channel_type=ch.type,
                channel_config=ch.config if isinstance(ch.config, dict) else {},
                payload=payload,
            ))
        await self.queue.enqueue_many(notifications)
        return []

    async def send_test_notification(self, channel: AlertChannel, payload: NotificationPayload) -> DeliveryResult:
        """Send a test notification to a specific channel."""
        qn = QueuedNotification(
            channel_id=channel.id,
            channel_type=channel.type,
            channel_config=channel.config if isinstance(channel.config, dict) else {},
            payload=payload,
            max_attempts=1,
        )
        result = await self.queue._deliver_single(qn)
        return result

    def get_stats(self) -> Dict[str, Any]:
        return self.queue.get_delivery_stats()


# ── Module-level singleton ────────────────────────────────────────────────────

_notification_service: Optional[NotificationService] = None


def get_notification_service() -> NotificationService:
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service
