
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
