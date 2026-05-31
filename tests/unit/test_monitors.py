
"""Monitor API endpoint tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_monitor(client: AsyncClient, test_user: dict):
    """Test creating a new monitor."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    response = await client.post("/api/v1/monitors", json={
        "name": "Google",
        "url": "https://google.com",
        "type": "https",
        "interval_seconds": 60,
        "expected_status_code": 200,
    }, headers=headers)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Google"
    assert data["url"] == "https://google.com"
    assert data["type"] == "https"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_create_monitor_invalid_url(client: AsyncClient, test_user: dict):
    """Test creating monitor with invalid URL fails."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    response = await client.post("/api/v1/monitors", json={
        "name": "Bad URL",
        "url": "ftp://invalid.com",
        "type": "http",
    }, headers=headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_monitors(client: AsyncClient, test_user: dict):
    """Test listing monitors."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    # Create a monitor first
    await client.post("/api/v1/monitors", json={
        "name": "Test Site",
        "url": "https://example.com",
        "type": "https",
    }, headers=headers)

    response = await client.get("/api/v1/monitors", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["items"]) >= 1


@pytest.mark.asyncio
async def test_get_monitor(client: AsyncClient, test_user: dict):
    """Test getting a specific monitor."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    create_resp = await client.post("/api/v1/monitors", json={
        "name": "Get Test",
        "url": "https://example.com",
        "type": "https",
    }, headers=headers)
    monitor_id = create_resp.json()["id"]

    response = await client.get(f"/api/v1/monitors/{monitor_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == monitor_id


@pytest.mark.asyncio
async def test_get_nonexistent_monitor(client: AsyncClient, test_user: dict):
    """Test getting a monitor that doesn't exist."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    response = await client.get("/api/v1/monitors/nonexistent-id", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_monitor(client: AsyncClient, test_user: dict):
    """Test updating a monitor."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    create_resp = await client.post("/api/v1/monitors", json={
        "name": "Update Test",
        "url": "https://example.com",
        "type": "https",
    }, headers=headers)
    monitor_id = create_resp.json()["id"]

    response = await client.patch(f"/api/v1/monitors/{monitor_id}", json={
        "name": "Updated Name",
        "interval_seconds": 120,
    }, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"
    assert data["interval_seconds"] == 120


@pytest.mark.asyncio
async def test_delete_monitor(client: AsyncClient, test_user: dict):
    """Test deleting a monitor."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    create_resp = await client.post("/api/v1/monitors", json={
        "name": "Delete Test",
        "url": "https://example.com",
        "type": "https",
    }, headers=headers)
    monitor_id = create_resp.json()["id"]

    response = await client.delete(f"/api/v1/monitors/{monitor_id}", headers=headers)
    assert response.status_code == 204

    # Verify it's gone
    get_resp = await client.get(f"/api/v1/monitors/{monitor_id}", headers=headers)
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_pause_resume_monitor(client: AsyncClient, test_user: dict):
    """Test pausing and resuming a monitor."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    create_resp = await client.post("/api/v1/monitors", json={
        "name": "Pause Test",
        "url": "https://example.com",
        "type": "https",
    }, headers=headers)
    monitor_id = create_resp.json()["id"]

    # Pause
    pause_resp = await client.post(f"/api/v1/monitors/{monitor_id}/pause", headers=headers)
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "paused"
    assert pause_resp.json()["is_active"] is False

    # Resume
    resume_resp = await client.post(f"/api/v1/monitors/{monitor_id}/resume", headers=headers)
    assert resume_resp.status_code == 200
    assert resume_resp.json()["status"] == "pending"
    assert resume_resp.json()["is_active"] is True


@pytest.mark.asyncio
async def test_list_monitors_pagination(client: AsyncClient, test_user: dict):
    """Test monitor listing pagination."""
    headers = {"Authorization": f"Bearer {test_user['token']}"}
    response = await client.get("/api/v1/monitors?page=1&page_size=10", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 10


@pytest.mark.asyncio
async def test_unauthenticated_access(client: AsyncClient):
    """Test that unauthenticated requests are rejected."""
    response = await client.get("/api/v1/monitors")
    assert response.status_code == 401
