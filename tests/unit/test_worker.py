
"""Check worker tests."""

from __future__ import annotations

import pytest
from src.workers.check_worker import CheckResult, HTTPChecker, TCPChecker, ICMPChecker, DNSChecker


class TestCheckResult:
    """Tests for CheckResult data class."""

    def test_success_result(self):
        result = CheckResult(is_up=True, status_code=200, response_time_ms=45.5)
        assert result.is_up is True
        assert result.status_code == 200
        assert result.response_time_ms == 45.5
        assert result.error_message is None

    def test_failure_result(self):
        result = CheckResult(is_up=False, error_message="Connection refused")
        assert result.is_up is False
        assert result.error_message == "Connection refused"

    def test_full_result(self):
        result = CheckResult(
            is_up=True,
            status_code=200,
            response_time_ms=123.45,
            dns_resolution_ms=12.3,
            tls_handshake_ms=45.6,
            ttfb_ms=78.9,
            content_length=1024,
            headers={"Content-Type": "text/html"},
        )
        assert result.dns_resolution_ms == 12.3
        assert result.tls_handshake_ms == 45.6
        assert result.ttfb_ms == 78.9
        assert result.content_length == 1024
        assert result.headers["Content-Type"] == "text/html"


class TestHTTPChecker:
    """Tests for HTTP checker."""

    def test_checker_creation(self):
        checker = HTTPChecker()
        assert checker is not None

    def test_parse_headers_valid(self):
        import json
        headers = json.dumps({"Authorization": "Bearer token", "X-Custom": "value"})
        parsed = HTTPChecker._parse_headers(headers)
        assert parsed["Authorization"] == "Bearer token"
        assert parsed["X-Custom"] == "value"

    def test_parse_headers_empty(self):
        assert HTTPChecker._parse_headers(None) == {}
        assert HTTPChecker._parse_headers("") == {}

    def test_parse_headers_invalid_json(self):
        """Invalid JSON should return empty dict."""
        result = HTTPChecker._parse_headers("not json")
        assert result == {}


class TestTCPChecker:
    """Tests for TCP checker."""

    def test_checker_creation(self):
        checker = TCPChecker()
        assert checker is not None


class TestICMPChecker:
    """Tests for ICMP checker."""

    def test_checker_creation(self):
        checker = ICMPChecker()
        assert checker is not None


class TestDNSChecker:
    """Tests for DNS checker."""

    def test_checker_creation(self):
        checker = DNSChecker()
        assert checker is not None


class TestSSLInfo:
    """Tests for SSL info data class."""

    def test_ssl_info_defaults(self):
        from src.workers.check_worker import SSLInfo
        info = SSLInfo()
        assert info.is_valid is True
        assert info.days_remaining == 0

    def test_ssl_info_populated(self):
        from src.workers.check_worker import SSLInfo
        info = SSLInfo(
            issuer="Let's Encrypt",
            subject="example.com",
            expires_at="2025-12-31",
            days_remaining=180,
            is_valid=True,
            protocol="TLS 1.3",
        )
        assert info.issuer == "Let's Encrypt"
        assert info.days_remaining == 180
        assert info.protocol == "TLS 1.3"
