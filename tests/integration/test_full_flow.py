
"""Integration tests — full user flow."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_complete_monitor_flow(client: AsyncClient):
    """Test complete flow: register → create monitor → check → delete."""
    # 1. Register
    reg = await client.post("/api/v1/auth/register", json={
        "email": "flow@example.com",
        "username": "flowuser",
        "password": "SecurePass123",
    })
    assert reg.status_code == 201
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Create monitor
    create = await client.post("/api/v1/monitors", json={
        "name": "Flow Test Monitor",
        "url": "https://httpbin.org",
        "type": "https",
        "interval_seconds": 30,
    }, headers=headers)
    assert create.status_code == 201
    monitor_id = create.json()["id"]

    # 3. Get monitor
    get = await client.get(f"/api/v1/monitors/{monitor_id}", headers=headers)
    assert get.status_code == 200
    assert get.json()["name"] == "Flow Test Monitor"

    # 4. List monitors
    listing = await client.get("/api/v1/monitors", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1

    # 5. Pause monitor
    pause = await client.post(f"/api/v1/monitors/{monitor_id}/pause", headers=headers)
    assert pause.status_code == 200
    assert pause.json()["status"] == "paused"

    # 6. Update monitor
    update = await client.patch(f"/api/v1/monitors/{monitor_id}", json={
        "name": "Updated Flow Monitor",
    }, headers=headers)
    assert update.status_code == 200
    assert update.json()["name"] == "Updated Flow Monitor"

    # 7. Delete monitor
    delete = await client.delete(f"/api/v1/monitors/{monitor_id}", headers=headers)
    assert delete.status_code == 204


@pytest.mark.asyncio
async def test_team_flow(client: AsyncClient):
    """Test team creation and member management flow."""
    # Register user
    reg = await client.post("/api/v1/auth/register", json={
        "email": "teamflow@example.com",
        "username": "teamflow",
        "password": "SecurePass123",
    })
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create team
    create = await client.post("/api/v1/teams", json={
        "name": "Flow Team",
        "slug": "flow-team",
        "description": "Integration test team",
    }, headers=headers)
    assert create.status_code == 201
    team_id = create.json()["id"]

    # List teams
    listing = await client.get("/api/v1/teams", headers=headers)
    assert listing.status_code == 200
    assert len(listing.json()) >= 1

    # Get team details
    get = await client.get(f"/api/v1/teams/{team_id}", headers=headers)
    assert get.status_code == 200
    assert get.json()["name"] == "Flow Team"


@pytest.mark.asyncio
async def test_dashboard_flow(client: AsyncClient):
    """Test dashboard creation and widget management."""
    # Setup user and monitor
    reg = await client.post("/api/v1/auth/register", json={
        "email": "dashflow@example.com",
        "username": "dashflow",
        "password": "SecurePass123",
    })
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    mon = await client.post("/api/v1/monitors", json={
        "name": "Dash Monitor",
        "url": "https://example.com",
    }, headers=headers)
    monitor_id = mon.json()["id"]

    # Create dashboard
    create = await client.post("/api/v1/dashboards", json={
        "name": "Test Dashboard",
        "slug": "test-dash",
        "columns": 3,
        "theme": "dark",
    }, headers=headers)
    assert create.status_code == 201
    dash_id = create.json()["id"]

    # Add widget
    widget = await client.post(f"/api/v1/dashboards/{dash_id}/widgets", json={
        "name": "Monitor Status",
        "widget_type": "status_piece",
        "monitor_ids": [monitor_id],
    }, headers=headers)
    assert widget.status_code == 201

    # Get dashboard with data
    get = await client.get(f"/api/v1/dashboards/{dash_id}", headers=headers)
    assert get.status_code == 200


@pytest.mark.asyncio
async def test_unauthorized_cannot_access_others_resources(client: AsyncClient):
    """Test that users cannot access each other's resources."""
    # User 1 creates a monitor
    reg1 = await client.post("/api/v1/auth/register", json={
        "email": "user1@example.com",
        "username": "user1",
        "password": "SecurePass123",
    })
    token1 = reg1.json()["access_token"]
    headers1 = {"Authorization": f"Bearer {token1}"}

    mon = await client.post("/api/v1/monitors", json={
        "name": "Private Monitor",
        "url": "https://example.com",
    }, headers=headers1)
    monitor_id = mon.json()["id"]

    # User 2 registers
    reg2 = await client.post("/api/v1/auth/register", json={
        "email": "user2@example.com",
        "username": "user2",
        "password": "SecurePass123",
    })
    token2 = reg2.json()["access_token"]
    headers2 = {"Authorization": f"Bearer {token2}"}

    # User 2 cannot see user 1's monitor
    response = await client.get(f"/api/v1/monitors/{monitor_id}", headers=headers2)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_health_endpoint_no_auth(client: AsyncClient):
    """Test that health endpoint is publicly accessible."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
