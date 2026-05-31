
"""Authentication endpoint tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_user(client: AsyncClient):
    """Test successful user registration."""
    response = await client.post("/api/v1/auth/register", json={
        "email": "newuser@example.com",
        "username": "newuser",
        "password": "SecurePass123",
        "full_name": "New User",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["user"]["email"] == "newuser@example.com"
    assert data["user"]["username"] == "newuser"
    assert data["access_token"]
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient):
    """Test registration with duplicate email fails."""
    payload = {
        "email": "dup@example.com",
        "username": "dupuser1",
        "password": "SecurePass123",
    }
    await client.post("/api/v1/auth/register", json=payload)
    payload["username"] = "dupuser2"
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_register_weak_password(client: AsyncClient):
    """Test registration with weak password fails validation."""
    response = await client.post("/api/v1/auth/register", json={
        "email": "weak@example.com",
        "username": "weakuser",
        "password": "short",
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    """Test successful login."""
    # Register first
    await client.post("/api/v1/auth/register", json={
        "email": "login@example.com",
        "username": "loginuser",
        "password": "SecurePass123",
    })
    # Login
    response = await client.post("/api/v1/auth/login", json={
        "email_or_username": "login@example.com",
        "password": "SecurePass123",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["access_token"]
    assert data["user"]["email"] == "login@example.com"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    """Test login with wrong password fails."""
    await client.post("/api/v1/auth/register", json={
        "email": "wrong@example.com",
        "username": "wronguser",
        "password": "SecurePass123",
    })
    response = await client.post("/api/v1/auth/login", json={
        "email_or_username": "wrong@example.com",
        "password": "WrongPass123",
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_by_username(client: AsyncClient):
    """Test login using username instead of email."""
    await client.post("/api/v1/auth/register", json={
        "email": "byname@example.com",
        "username": "bynameuser",
        "password": "SecurePass123",
    })
    response = await client.post("/api/v1/auth/login", json={
        "email_or_username": "bynameuser",
        "password": "SecurePass123",
    })
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    """Test health check endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data


@pytest.mark.asyncio
async def test_password_validation_requirements(client: AsyncClient):
    """Test that password validation enforces complexity requirements."""
    test_cases = [
        ("lowercase123", "uppercase"),
        ("UPPERCASE123", "lowercase"),
        ("NoDigitsHere", "digit"),
    ]
    for i, (password, expected_error) in enumerate(test_cases):
        response = await client.post("/api/v1/auth/register", json={
            "email": f"pwtest{i}@example.com",
            "username": f"pwtest{i}",
            "password": password,
        })
        assert response.status_code == 422, f"Password '{password}' should fail validation"


@pytest.mark.asyncio
async def test_register_invalid_email(client: AsyncClient):
    """Test registration with invalid email format."""
    response = await client.post("/api/v1/auth/register", json={
        "email": "not-an-email",
        "username": "bademail",
        "password": "SecurePass123",
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_invalid_username(client: AsyncClient):
    """Test registration with invalid username characters."""
    response = await client.post("/api/v1/auth/register", json={
        "email": "badname@example.com",
        "username": "bad name!@#",
        "password": "SecurePass123",
    })
    assert response.status_code == 422
