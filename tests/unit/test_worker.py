"""Check worker tests."""

from __future__ import annotations

import pytest
from src.workers.check_worker import (
    CheckResult, HTTPChecker, TCPChecker, ICMPChecker, DNSChecker,
    SSLInfo, TimingBreakdown, CheckRunner,
)


class TestCheckResult:
    """Tests for CheckResult data class."""

    def test_success_result(self):
        result = CheckResult(
            monitor_id="mon-1",
            monitor_type="http",
            is_up=True,
            status_code=200,
            response_time_ms=45.5,
        )
        assert result.is_up is True
        assert result.status_code == 200
        assert result.response_time_ms == 45.5
        assert result.error is None

    def test_failure_result(self):
        result = CheckResult(
            monitor_id="mon-1",
            monitor_type="http",
            is_up=False,
            error="Connection refused",
        )
        assert result.is_up is False
        assert result.error == "Connection refused"

    def test_full_result(self):
        timing = TimingBreakdown(
            dns_ms=12.3,
            tcp_ms=20.0,
            tls_ms=45.6,
            ttfb_ms=78.9,
            total_ms=123.45,
        )
        result = CheckResult(
            monitor_id="mon-1",
            monitor_type="http",
            is_up=True,
            status_code=200,
            response_time_ms=123.45,
            timing=timing,
            headers_sent={"User-Agent": "Pulse"},
            headers_received={"Content-Type": "text/html"},
            body_size_bytes=1024,
        )
        assert result.timing.dns_ms == 12.3
        assert result.timing.tls_ms == 45.6
        assert result.timing.ttfb_ms == 78.9
        assert result.body_size_bytes == 1024
        assert result.headers_received["Content-Type"] == "text/html"
        assert result.checked_at is not None


class TestTimingBreakdown:
    """Tests for TimingBreakdown data class."""

    def test_defaults(self):
        t = TimingBreakdown()
        assert t.dns_ms == 0.0
        assert t.tcp_ms == 0.0
        assert t.tls_ms == 0.0
        assert t.ttfb_ms == 0.0
        assert t.total_ms == 0.0

    def test_populated(self):
        t = TimingBreakdown(
            dns_ms=5.0,
            tcp_ms=10.0,
            tls_ms=20.0,
            ttfb_ms=50.0,
            total_ms=85.0,
        )
        assert t.dns_ms == 5.0
        assert t.tcp_ms == 10.0
        assert t.total_ms == 85.0


class TestHTTPChecker:
    """Tests for HTTP checker."""

    def test_checker_creation(self):
        checker = HTTPChecker()
        assert checker is not None
        assert hasattr(checker, 'check')

    def test_has_async_check_method(self):
        checker = HTTPChecker()
        import asyncio
        assert asyncio.iscoroutinefunction(checker.check)


class TestTCPChecker:
    """Tests for TCP checker."""

    def test_checker_creation(self):
        checker = TCPChecker()
        assert checker is not None
        assert hasattr(checker, 'check')


class TestICMPChecker:
    """Tests for ICMP checker."""

    def test_checker_creation(self):
        checker = ICMPChecker()
        assert checker is not None
        assert hasattr(checker, 'check')


class TestDNSChecker:
    """Tests for DNS checker."""

    def test_checker_creation(self):
        checker = DNSChecker()
        assert checker is not None
        assert hasattr(checker, 'check')


class TestSSLInfo:
    """Tests for SSL info data class."""

    def test_ssl_info_defaults(self):
        info = SSLInfo()
        assert info.is_valid is True
        assert info.days_until_expiry is None
        assert info.issuer is None

    def test_ssl_info_populated(self):
        info = SSLInfo(
            issuer="Let's Encrypt",
            subject="example.com",
            days_until_expiry=180,
            is_valid=True,
            protocol="TLS 1.3",
        )
        assert info.issuer == "Let's Encrypt"
        assert info.days_until_expiry == 180
        assert info.protocol == "TLS 1.3"


class TestCheckRunner:
    """Tests for CheckRunner orchestrator."""

    def test_runner_creation(self):
        runner = CheckRunner()
        assert runner is not None

    def test_runner_has_checkers_dict(self):
        runner = CheckRunner()
        assert hasattr(runner, 'checkers')
        assert isinstance(runner.checkers, dict)
        assert len(runner.checkers) >= 6

    def test_runner_has_required_checker_types(self):
        runner = CheckRunner()
        from src.models.monitor import MonitorType
        for mt in [MonitorType.HTTP, MonitorType.HTTPS, MonitorType.TCP, MonitorType.ICMP, MonitorType.DNS]:
            assert mt in runner.checkers or str(mt).lower() in runner.checkers, f"Missing checker for {mt}"

    def test_runner_has_async_check(self):
        runner = CheckRunner()
        import asyncio
        assert asyncio.iscoroutinefunction(runner.check)
