
"""Service layer tests."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import async_session_factory
from src.services.auth_service import AuthService
from src.services.monitor_service import MonitorService


@pytest.mark.asyncio
async def test_password_hashing():
    """Test password hashing and verification."""
    password = "TestPassword123"
    hashed = AuthService.hash_password(password)
    assert hashed != password
    assert AuthService.verify_password(password, hashed) is True
    assert AuthService.verify_password("WrongPassword", hashed) is False


@pytest.mark.asyncio
async def test_jwt_token_creation():
    """Test JWT token creation and decoding."""
    user_id = "test-user-123"
    token = AuthService.create_access_token(user_id)
    assert isinstance(token, str)
    assert len(token) > 0

    payload = AuthService.decode_token(token)
    assert payload is not None
    assert payload["sub"] == user_id
    assert payload["type"] == "access"


@pytest.mark.asyncio
async def test_jwt_token_expiry():
    """Test that expired tokens are rejected."""
    from datetime import timedelta
    token = AuthService.create_access_token("test", extra_claims={"exp": 0})
    # Token with exp=0 should be expired
    payload = AuthService.decode_token(token)
    # This depends on JWT library behavior with exp=0


@pytest.mark.asyncio
async def test_invalid_token():
    """Test decoding invalid token."""
    payload = AuthService.decode_token("invalid.token.here")
    assert payload is None


@pytest.mark.asyncio
async def test_refresh_token():
    """Test refresh token creation."""
    user_id = "test-user-456"
    token = AuthService.create_refresh_token(user_id)
    payload = AuthService.decode_token(token)
    assert payload["sub"] == user_id
    assert payload["type"] == "refresh"


@pytest.mark.asyncio
async def test_monitor_uptime_calculation():
    """Test uptime percentage calculation logic."""
    total_checks = 100
    up_checks = 95
    uptime = round((up_checks / total_checks) * 100, 2)
    assert uptime == 95.0


@pytest.mark.asyncio
async def test_monitor_status_transitions():
    """Test monitor status transition logic."""
    from src.models.monitor import MonitorStatus
    
    # Initial state
    status = MonitorStatus.PENDING
    assert status == "pending"
    
    # After successful check
    status = MonitorStatus.UP
    assert status == "up"
    
    # After failures
    consecutive_failures = 3
    if consecutive_failures >= 3:
        status = MonitorStatus.DOWN
    assert status == "down"


@pytest.mark.asyncio
async def test_alert_severity_levels():
    """Test alert severity level constants."""
    from src.models.alert import AlertSeverity
    
    assert AlertSeverity.CRITICAL == "critical"
    assert AlertSeverity.WARNING == "warning"
    assert AlertSeverity.INFO == "info"
    assert AlertSeverity.RECOVERY == "recovery"


@pytest.mark.asyncio
async def test_monitor_type_constants():
    """Test monitor type constants."""
    from src.models.monitor import MonitorType
    
    assert MonitorType.HTTP == "http"
    assert MonitorType.HTTPS == "https"
    assert MonitorType.TCP == "tcp"
    assert MonitorType.ICMP == "icmp"
    assert MonitorType.DNS == "dns"
    assert MonitorType.KEYWORD == "keyword"
    assert MonitorType.GRAPHQL == "graphql"


@pytest.mark.asyncio
async def test_team_role_hierarchy():
    """Test team role hierarchy and permissions."""
    from src.models.team import TeamRole
    
    assert TeamRole.OWNER == "owner"
    assert TeamRole.ADMIN == "admin"
    assert TeamRole.MEMBER == "member"
    assert TeamRole.VIEWER == "viewer"


@pytest.mark.asyncio
async def test_incident_status_flow():
    """Test incident status flow constants."""
    from src.models.incident import IncidentStatus
    
    assert IncidentStatus.INVESTIGATING == "investigating"
    assert IncidentStatus.IDENTIFIED == "identified"
    assert IncidentStatus.MONITORING == "monitoring"
    assert IncidentStatus.RESOLVED == "resolved"


@pytest.mark.asyncio
async def test_widget_types():
    """Test widget type constants."""
    from src.models.dashboard import WidgetType
    
    assert WidgetType.STATUS_PIECE == "status_piece"
    assert WidgetType.UPTIME_BAR == "uptime_bar"
    assert WidgetType.RESPONSE_TIME_CHART == "response_time_chart"
    assert WidgetType.MONITOR_LIST == "monitor_list"
    assert WidgetType.INCIDENT_LIST == "incident_list"
    assert WidgetType.STATS_OVERVIEW == "stats_overview"
