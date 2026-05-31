"""Notification delivery service."""

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

        text = f"*{title}*\n\n{message}"
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
