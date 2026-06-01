"""Pulse check worker — multi-type health check engine.

Supported monitor types:
  - HTTP/HTTPS — full HTTP method, headers, body, keyword matching, status code
  - TCP — TCP port connectivity
  - ICMP — ping echo request
  - DNS — A/AAAA/MX/NS/TXT record resolution
  - Keyword — HTTP check + keyword presence assertion
  - GraphQL — POST JSON GraphQL queries with variable support

Features:
  - Configurable timeout, retries, retry intervals
  - Response time tracking (DNS, TCP, TLS, TTFB, total)
  - HTTP header request/response capture
  - SSL/TLS info extraction (issuer, expiry, protocol)
  - Automatic monitor status transitions
  - Uptime percentage and SLA calculation
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import re
import socket
import ssl
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from src.config.settings import get_settings
from src.models.monitor import Monitor, MonitorCheck, MonitorStatus, MonitorType

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Check Result ──────────────────────────────────────────────────────────────

@dataclass
class SSLInfo:
    """SSL/TLS certificate information."""
    issuer: Optional[str] = None
    subject: Optional[str] = None
    expires_at: Optional[datetime] = None
    days_until_expiry: Optional[int] = None
    protocol: Optional[str] = None
    cipher: Optional[str] = None
    serial_number: Optional[str] = None
    fingerprint_sha256: Optional[str] = None
    is_valid: bool = True
    validation_error: Optional[str] = None


@dataclass
class TimingBreakdown:
    """Detailed timing for an HTTP check."""
    dns_ms: float = 0.0
    tcp_ms: float = 0.0
    tls_ms: float = 0.0
    ttfb_ms: float = 0.0
    total_ms: float = 0.0
    transfer_ms: float = 0.0


@dataclass
class CheckResult:
    """Result of a single health check attempt."""
    monitor_id: str
    monitor_type: str
    is_up: bool
    status_code: Optional[int] = None
    response_time_ms: float = 0.0
    timing: Optional[TimingBreakdown] = None
    headers_sent: Dict[str, str] = field(default_factory=dict)
    headers_received: Dict[str, str] = field(default_factory=dict)
    body_snippet: Optional[str] = None
    body_size_bytes: int = 0
    ssl_info: Optional[SSLInfo] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    keyword_found: Optional[bool] = None
    dns_records: List[str] = field(default_factory=list)
    resolved_ip: Optional[str] = None
    checked_at: datetime = field(default_factory=datetime.utcnow)
    attempt: int = 1
    retries_total: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_check_record(self) -> Dict[str, Any]:
        """Convert to dict for MonitorCheck ORM creation."""
        return {
            "monitor_id": self.monitor_id,
            "is_up": self.is_up,
            "status_code": self.status_code,
            "response_time_ms": round(self.response_time_ms, 2),
            "headers_sent": self.headers_sent,
            "headers_received": {k: v for k, v in self.headers_received.items()},
            "body_snippet": self.body_snippet[:500] if self.body_snippet else None,
            "body_size_bytes": self.body_size_bytes,
            "ssl_info": self.ssl_info.__dict__ if self.ssl_info else None,
            "error": self.error,
            "error_type": self.error_type,
            "keyword_found": self.keyword_found,
            "dns_records": self.dns_records,
            "resolved_ip": self.resolved_ip,
            "checked_at": self.checked_at,
            "attempt": self.attempt,
            "timing": self.timing.__dict__ if self.timing else None,
        }


# ── Status Determination ─────────────────────────────────────────────

class StatusDetermination:
    """Determine monitor status from check result."""

    @staticmethod
    def determine(result: CheckResult, monitor: Monitor) -> MonitorStatus:
        if result.is_up:
            return MonitorStatus.UP
        # Check if SSL-related failure
        if result.ssl_info and result.ssl_info.validation_error:
            return MonitorStatus.ERROR
        # Check if DNS failure
        if result.error_type == "dns_resolution":
            return MonitorStatus.DOWN
        # HTTP status code errors
        if result.status_code and result.status_code >= 500:
            return MonitorStatus.DOWN
        if result.status_code and result.status_code >= 400:
            return MonitorStatus.ERROR
        # Timeout
        if result.error_type == "timeout":
            return MonitorStatus.DOWN
        # Keyword not found
        if result.keyword_found is False:
            return MonitorStatus.DOWN
        return MonitorStatus.DOWN


# ── Response Body Processing ─────────────────────────────────────────────

def process_response_body(
    body: bytes,
    content_type: str = "",
    max_size: int = 65536,
) -> Tuple[Optional[str], int]:
    """Process and truncate response body for storage."""
    body_size = len(body)
    if body_size > max_size:
        body = body[:max_size]

    # Try to decode as text
    if "application/json" in content_type:
        try:
            parsed = json.loads(body.decode("utf-8", errors="replace"))
            snippet = json.dumps(parsed, indent=2)[:2000]
            return snippet, body_size
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    if any(ct in content_type for ct in ["text/", "application/xml", "application/xhtml"]):
        try:
            return body.decode("utf-8", errors="replace")[:2000], body_size
        except UnicodeDecodeError:
            pass

    # Binary content — return hex digest
    return f"[Binary: {body_size} bytes, sha256={hashlib.sha256(body).hexdigest()[:16]}]", body_size


# ── SSL Certificate Parser ──────────────────────────────────────────────

def parse_ssl_info(hostname: str, port: int = 443, timeout: float = 10.0) -> SSLInfo:
    """Connect and extract SSL certificate information."""
    info = SSLInfo()
    context = ssl.create_default_context()
    try:
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                info.protocol = ssock.version()
                info.cipher = ssock.cipher()[0] if ssock.cipher() else None

                if cert:
                    issuer = cert.get("issuer", ())
                    info.issuer = ", ".join(
                        f"{k}={v}" for part in issuer for k, v in part
                    ) if issuer else None
                    subject = cert.get("subject", ())
                    info.subject = ", ".join(
                        f"{k}={v}" for part in subject for k, v in part
                    ) if subject else None

                    expires = cert.get("notAfter")
                    if expires:
                        try:
                            info.expires_at = datetime.strptime(expires, "%b %d %H:%M:%S %Y %Z")
                            info.days_until_expiry = max(
                                0, (info.expires_at - datetime.utcnow()).days
                            )
                        except ValueError:
                            pass

                    info.serial_number = cert.get("serialNumber")
                    info.is_valid = True

                # Get fingerprint
                der_cert = ssock.getpeercert(binary_form=True)
                if der_cert:
                    info.fingerprint_sha256 = hashlib.sha256(der_cert).hexdigest()

    except ssl.SSLCertVerificationError as e:
        info.is_valid = False
        info.validation_error = str(e)
    except socket.timeout:
        info.is_valid = False
        info.validation_error = "SSL handshake timed out"
    except Exception as e:
        info.is_valid = False
        info.validation_error = str(e)

    return info


# ── HTTP/HTTPS Checker ────────────────────────────────────────────────────────

class HTTPChecker:
    """Full-featured HTTP/HTTPS endpoint checker."""

    async def check(self, monitor: Monitor) -> CheckResult:
        url = monitor.url
        method = (monitor.method or "GET").upper()
        timeout = float(monitor.timeout_seconds or 10)
        headers = dict(monitor.headers) if monitor.headers else {}
        body = monitor.body if hasattr(monitor, 'body') and monitor.body else None

        # Set default headers
        headers.setdefault("User-Agent", "Pulse-Monitor/1.0")
        headers.setdefault("Accept", "*/*")

        result = CheckResult(
            monitor_id=monitor.id,
            monitor_type=monitor.type,
            is_up=False,
            headers_sent=headers,
            timing=TimingBreakdown(),
        )

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                verify=True,
            ) as client:
                response = await client.request(method, url, headers=headers, content=body)
            total_ms = (time.monotonic() - start) * 1000

            result.status_code = response.status_code
            result.response_time_ms = total_ms
            result.timing.total_ms = total_ms
            result.headers_received = dict(response.headers)

            # Process body
            body_bytes = response.content
            content_type = response.headers.get("content-type", "")
            snippet, body_size = process_response_body(body_bytes, content_type)
            result.body_snippet = snippet
            result.body_size_bytes = body_size

            # Check expected status code
            expected = monitor.expected_status_code or 200
            result.is_up = response.status_code == expected

            # Check expected keyword
            if monitor.expected_keyword and result.is_up:
                try:
                    text = response.text
                    result.keyword_found = monitor.expected_keyword.lower() in text.lower()
                    if not result.keyword_found:
                        result.is_up = False
                        result.error = f"Expected keyword '{monitor.expected_keyword}' not found"
                except Exception as e:
                    result.keyword_found = False
                    result.error = f"Keyword check error: {e}"

            # SSL info for HTTPS
            if url.startswith("https://"):
                parsed = urlparse(url)
                try:
                    result.ssl_info = parse_ssl_info(parsed.hostname, parsed.port or 443)
                except Exception:
                    pass  # SSL info is optional

        except httpx.TimeoutException:
            result.error = f"Request timed out after {timeout}s"
            result.error_type = "timeout"
            result.response_time_ms = timeout * 1000
        except httpx.ConnectError as e:
            result.error = f"Connection error: {e}"
            result.error_type = "connection"
            result.response_time_ms = (time.monotonic() - start) * 1000
        except httpx.HTTPStatusError as e:
            result.status_code = e.response.status_code
            result.error = f"HTTP error: {e}"
            result.error_type = f"http_{e.response.status_code}"
            result.response_time_ms = (time.monotonic() - start) * 1000
        except Exception as e:
            result.error = str(e)
            result.error_type = "unknown"
            result.response_time_ms = (time.monotonic() - start) * 1000

        return result


# ── TCP Checker ───────────────────────────────────────────────────────────────

class TCPChecker:
    """TCP port connectivity checker."""

    async def check(self, monitor: Monitor) -> CheckResult:
        url = monitor.url
        timeout = float(monitor.timeout_seconds or 10)

        result = CheckResult(
            monitor_id=monitor.id,
            monitor_type="tcp",
            is_up=False,
        )

        # Parse host and port from URL
        parsed = urlparse(url)
        if parsed.hostname:
            host = parsed.hostname
            port = parsed.port or 80
        else:
            # Try host:port format
            if ":" in url:
                host, port_str = url.rsplit(":", 1)
                try:
                    port = int(port_str)
                except ValueError:
                    host, port = url, 80
            else:
                host, port = url, 80

        start = time.monotonic()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
            result.response_time_ms = (time.monotonic() - start) * 1000
            result.is_up = True
            result.resolved_ip = writer.get_extra_info("peername", (None,))[0]
            writer.close()
            await writer.wait_closed()
        except asyncio.TimeoutError:
            result.error = f"TCP connection timed out after {timeout}s to {host}:{port}"
            result.error_type = "timeout"
            result.response_time_ms = timeout * 1000
        except ConnectionRefusedError:
            result.error = f"Connection refused: {host}:{port}"
            result.error_type = "connection_refused"
            result.response_time_ms = (time.monotonic() - start) * 1000
        except OSError as e:
            result.error = f"TCP error: {e}"
            result.error_type = "os_error"
            result.response_time_ms = (time.monotonic() - start) * 1000
        except Exception as e:
            result.error = str(e)
            result.error_type = "unknown"
            result.response_time_ms = (time.monotonic() - start) * 1000

        return result


# ── ICMP Checker ──────────────────────────────────────────────────────────────

class ICMPChecker:
    """ICMP ping checker using system ping command."""

    async def check(self, monitor: Monitor) -> CheckResult:
        url = monitor.url
        timeout = float(monitor.timeout_seconds or 10)

        result = CheckResult(
            monitor_id=monitor.id,
            monitor_type="icmp",
            is_up=False,
        )

        parsed = urlparse(url)
        host = parsed.hostname or url.replace("icmp://", "").split("/")[0].split(":")[0]

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping", "-c", "1", "-W", str(int(timeout)), host,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
            elapsed = (time.monotonic() - start) * 1000
            result.response_time_ms = elapsed

            if proc.returncode == 0:
                result.is_up = True
                output = stdout.decode("utf-8", errors="replace")
                # Parse ping time
                time_match = re.search(r"time[=<]([\d.]+)\s*ms", output)
                if time_match:
                    result.response_time_ms = float(time_match.group(1))
                # Parse resolved IP
                ip_match = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)", output)
                if ip_match:
                    result.resolved_ip = ip_match.group(1)
            else:
                result.error = stderr.decode("utf-8", errors="replace").strip() or "Ping failed"
                result.error_type = "unreachable"

        except asyncio.TimeoutError:
            result.error = f"Ping timed out after {timeout}s"
            result.error_type = "timeout"
            result.response_time_ms = timeout * 1000
        except FileNotFoundError:
            result.error = "ping command not found"
            result.error_type = "not_found"
        except Exception as e:
            result.error = str(e)
            result.error_type = "unknown"
            result.response_time_ms = (time.monotonic() - start) * 1000

        return result


# ── DNS Checker ──────────────────────────────────────────────────────────────

class DNSChecker:
    """DNS resolution checker supporting A, AAAA, MX, NS, TXT, CNAME records."""

    RECORD_TYPES = {
        "A": 1,
        "AAAA": 28,
        "MX": 15,
        "NS": 2,
        "TXT": 16,
        "CNAME": 5,
        "SOA": 6,
    }

    async def check(self, monitor: Monitor) -> CheckResult:
        url = monitor.url
        timeout = float(monitor.timeout_seconds or 10)

        result = CheckResult(
            monitor_id=monitor.id,
            monitor_type="dns",
            is_up=False,
        )

        parsed = urlparse(url)
        hostname = parsed.hostname or url.replace("dns://", "").split("/")[0]

        # Determine record type from URL path or query
        record_type = "A"
        if parsed.path and parsed.path.strip("/"):
            rt = parsed.path.strip("/").upper()
            if rt in self.RECORD_TYPES:
                record_type = rt
        if parsed.query:
            for part in parsed.query.split("&"):
                if part.startswith("type="):
                    rt = part.split("=", 1)[1].upper()
                    if rt in self.RECORD_TYPES:
                        record_type = rt

        start = time.monotonic()
        try:
            import dns.resolver
            resolver = dns.resolver.Resolver()
            resolver.timeout = timeout
            resolver.lifetime = timeout

            records = resolver.resolve(hostname, record_type)
            result.response_time_ms = (time.monotonic() - start) * 1000
            result.is_up = True

            for record in records:
                record_text = str(record)
                result.dns_records.append(record_text)

                # Also get A record for IP
                if record_type == "A" and not result.resolved_ip:
                    result.resolved_ip = record_text

            if not result.resolved_ip and record_type in ("CNAME", "MX", "NS"):
                # Resolve the CNAME/MX target to IP
                try:
                    target = result.dns_records[0].split()[-1].rstrip(".")
                    a_records = resolver.resolve(target, "A")
                    result.resolved_ip = str(a_records[0])
                except Exception:
                    pass

        except ImportError:
            # Fallback to socket if dnspython not installed
            result = await self._fallback_check(hostname, result, timeout)
        except dns.resolver.NXDOMAIN:
            result.error = f"Domain {hostname} does not exist (NXDOMAIN)"
            result.error_type = "nxdomain"
            result.response_time_ms = (time.monotonic() - start) * 1000
        except dns.resolver.NoAnswer:
            result.error = f"No {record_type} records found for {hostname}"
            result.error_type = "no_answer"
            result.response_time_ms = (time.monotonic() - start) * 1000
        except dns.resolver.Timeout:
            result.error = f"DNS resolution timed out after {timeout}s"
            result.error_type = "timeout"
            result.response_time_ms = timeout * 1000
        except Exception as e:
            result.error = str(e)
            result.error_type = "unknown"
            result.response_time_ms = (time.monotonic() - start) * 1000

        return result

    async def _fallback_check(self, hostname: str, result: CheckResult, timeout: float) -> CheckResult:
        """Fallback DNS check using socket.getaddrinfo."""
        start = time.monotonic()
        try:
            loop = asyncio.get_event_loop()
            infos = await asyncio.wait_for(
                loop.getaddrinfo(hostname, None),
                timeout=timeout,
            )
            result.response_time_ms = (time.monotonic() - start) * 1000
            result.is_up = True
            seen = set()
            for info in infos:
                ip = info[4][0]
                if ip not in seen:
                    seen.add(ip)
                    result.dns_records.append(ip)
            if result.dns_records:
                result.resolved_ip = result.dns_records[0]
        except asyncio.TimeoutError:
            result.error = f"DNS resolution timed out after {timeout}s"
            result.error_type = "timeout"
            result.response_time_ms = timeout * 1000
        except socket.gaierror as e:
            result.error = f"DNS resolution failed: {e}"
            result.error_type = "dns_resolution"
            result.response_time_ms = (time.monotonic() - start) * 1000
        return result


# ── Keyword Checker ───────────────────────────────────────────────────────────

class KeywordChecker:
    """HTTP check with keyword presence/absence assertion."""

    def __init__(self):
        self.http_checker = HTTPChecker()

    async def check(self, monitor: Monitor) -> CheckResult:
        result = await self.http_checker.check(monitor)

        if result.is_up and monitor.expected_keyword:
            # Already handled in HTTPChecker
            result.monitor_type = "keyword"
        elif not monitor.expected_keyword:
            result.keyword_found = None

        result.monitor_type = "keyword"
        return result


# ── GraphQL Checker ───────────────────────────────────────────────────────────

class GraphQLChecker:
    """GraphQL endpoint checker with query and variable support."""

    async def check(self, monitor: Monitor) -> CheckResult:
        url = monitor.url
        timeout = float(monitor.timeout_seconds or 10)

        result = CheckResult(
            monitor_id=monitor.id,
            monitor_type="graphql",
            is_up=False,
            headers_sent={},
            timing=TimingBreakdown(),
        )

        # Extract query from monitor config
        query = None
        variables = {}
        operation_name = None

        if hasattr(monitor, 'headers') and monitor.headers and 'graphql_query' in monitor.headers:
            query = monitor.headers.get('graphql_query')
        if hasattr(monitor, 'body') and monitor.body:
            try:
                body_data = json.loads(monitor.body) if isinstance(monitor.body, str) else monitor.body
                query = body_data.get("query", query)
                variables = body_data.get("variables", {})
                operation_name = body_data.get("operationName")
            except (json.JSONDecodeError, AttributeError):
                pass

        if not query:
            # Default health check query
            query = "{ __typename }"

        graphql_body = {"query": query}
        if variables:
            graphql_body["variables"] = variables
        if operation_name:
            graphql_body["operationName"] = operation_name

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Pulse-Monitor/1.0",
            "Accept": "application/json",
        }
        headers.update(monitor.headers or {})

        result.headers_sent = {k: v for k, v in headers.items() if k != "graphql_query"}

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.post(url, json=graphql_body, headers=headers)
            total_ms = (time.monotonic() - start) * 1000

            result.status_code = response.status_code
            result.response_time_ms = total_ms
            result.timing.total_ms = total_ms
            result.headers_received = dict(response.headers)

            # Process response body
            body_bytes = response.content
            content_type = response.headers.get("content-type", "")
            snippet, body_size = process_response_body(body_bytes, content_type)
            result.body_snippet = snippet
            result.body_size_bytes = body_size

            # GraphQL returns 200 even for errors — check response body
            result.is_up = response.status_code == 200
            if result.is_up:
                try:
                    resp_json = response.json()
                    if "errors" in resp_json and resp_json["errors"]:
                        result.is_up = False
                        error_msgs = "; ".join(
                            e.get("message", str(e)) for e in resp_json["errors"][:3]
                        )
                        result.error = f"GraphQL errors: {error_msgs}"
                        result.error_type = "graphql_error"
                    if "data" not in resp_json and "errors" not in resp_json:
                        result.is_up = False
                        result.error = "GraphQL response missing data and errors"
                        result.error_type = "graphql_invalid"
                except (json.JSONDecodeError, ValueError):
                    result.is_up = False
                    result.error = "Invalid JSON in GraphQL response"
                    result.error_type = "json_decode"

            # Check keyword in response if configured
            if monitor.expected_keyword and result.is_up:
                try:
                    text = response.text
                    result.keyword_found = monitor.expected_keyword.lower() in text.lower()
                    if not result.keyword_found:
                        result.is_up = False
                        result.error = f"Expected keyword '{monitor.expected_keyword}' not found"
                except Exception as e:
                    result.keyword_found = False

        except httpx.TimeoutException:
            result.error = f"GraphQL request timed out after {timeout}s"
            result.error_type = "timeout"
            result.response_time_ms = timeout * 1000
        except Exception as e:
            result.error = str(e)
            result.error_type = "unknown"
            result.response_time_ms = (time.monotonic() - start) * 1000

        return result


# ── SSL Expiry Checker ────────────────────────────────────────────────────────

class SSLChecker:
    """SSL certificate expiry checker."""

    async def check(self, monitor: Monitor) -> CheckResult:
        url = monitor.url
        timeout = float(monitor.timeout_seconds or 10)

        result = CheckResult(
            monitor_id=monitor.id,
            monitor_type="ssl",
            is_up=False,
        )

        parsed = urlparse(url if "://" in url else f"https://{url}")
        hostname = parsed.hostname or url
        port = parsed.port or 443

        try:
            result.ssl_info = await asyncio.get_event_loop().run_in_executor(
                None, parse_ssl_info, hostname, port, timeout
            )
            if result.ssl_info.is_valid:
                result.is_up = True
                if result.ssl_info.days_until_expiry is not None:
                    # Warn if expiring within 30 days
                    if result.ssl_info.days_until_expiry <= 0:
                        result.is_up = False
                        result.error = f"SSL certificate expired {abs(result.ssl_info.days_until_expiry)} days ago"
                        result.error_type = "ssl_expired"
                    elif result.ssl_info.days_until_expiry <= 7:
                        result.metadata["ssl_warning"] = f"SSL expires in {result.ssl_info.days_until_expiry} days"
            else:
                result.error = result.ssl_info.validation_error or "SSL certificate validation failed"
                result.error_type = "ssl_invalid"
        except Exception as e:
            result.error = f"SSL check failed: {e}"
            result.error_type = "ssl_error"

        result.response_time_ms = 0  # Not meaningful for SSL
        return result


# ── Check Runner (dispatcher) ─────────────────────────────────────────────────

class CheckRunner:
    """Dispatches checks based on monitor type."""

    def __init__(self):
        self.checkers = {
            MonitorType.HTTP: HTTPChecker(),
            MonitorType.HTTPS: HTTPChecker(),
            MonitorType.TCP: TCPChecker(),
            MonitorType.ICMP: ICMPChecker(),
            MonitorType.DNS: DNSChecker(),
            MonitorType.KEYWORD: KeywordChecker(),
            MonitorType.GRAPHQL: GraphQLChecker(),
            "ssl": SSLChecker(),
        }

    async def check(self, monitor: Monitor) -> CheckResult:
        """Run a single check for the given monitor."""
        monitor_type = monitor.type.lower() if isinstance(monitor.type, str) else str(monitor.type).lower()
        checker = self.checkers.get(monitor_type)

        if not checker:
            return CheckResult(
                monitor_id=monitor.id,
                monitor_type=monitor_type,
                is_up=False,
                error=f"Unknown monitor type: {monitor_type}",
                error_type="config_error",
            )

        max_retries = monitor.retries or 1
        retry_interval = 5.0
        last_result = None

        for attempt in range(1, max_retries + 1):
            try:
                result = await checker.check(monitor)
                result.attempt = attempt
                result.retries_total = max_retries - 1

                if result.is_up or attempt == max_retries:
                    return result

                last_result = result
                if attempt < max_retries:
                    await asyncio.sleep(retry_interval)
            except Exception as e:
                last_result = CheckResult(
                    monitor_id=monitor.id,
                    monitor_type=monitor_type,
                    is_up=False,
                    error=str(e),
                    error_type="exception",
                    attempt=attempt,
                )
                if attempt < max_retries:
                    await asyncio.sleep(retry_interval)

        return last_result or CheckResult(
            monitor_id=monitor.id,
            monitor_type=monitor_type,
            is_up=False,
            error="All attempts failed",
            error_type="all_failed",
            attempt=max_retries,
        )


# ── Module-level singleton ────────────────────────────────────────────────────

_check_runner: Optional[CheckRunner] = None


def get_check_runner() -> CheckRunner:
    global _check_runner
    if _check_runner is None:
        _check_runner = CheckRunner()
    return _check_runner


# ── Backward Compatibility Alias ──────────────────────────────────────────────

CheckExecutor = CheckRunner
