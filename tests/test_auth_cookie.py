"""Tests for the httpOnly-cookie auth flow.

Backend was migrated to dual-mode auth: /auth/login and /auth/register set an
httpOnly `token` cookie AND return the JWT in the response body, and
get_current_user accepts either the cookie or the Authorization: Bearer header.
This preserves the existing localStorage frontend while the cookie path is
ready for the FE migration.
"""

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SUPABASE_JWT_SECRET", "pytest-placeholder-secret")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.auth import AUTH_COOKIE_NAME  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: F401, E402  - registers table on Base


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    User.__table__.create(engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    engine.dispose()


# ── /auth/register ────────────────────────────────────────────────────────────


def test_register_returns_token_and_sets_cookie(client):
    r = client.post(
        "/auth/register",
        json={"email": "a@example.com", "password": "hunter2!"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]

    cookie = r.cookies.get(AUTH_COOKIE_NAME)
    assert cookie == body["access_token"]

    set_cookie = r.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie
    assert "Path=/" in set_cookie
    assert (
        "SameSite=strict" in set_cookie.lower()
        or "samesite=strict" in set_cookie.lower()
    )


def test_register_duplicate_email(client):
    payload = {"email": "dup@example.com", "password": "password1"}
    assert client.post("/auth/register", json=payload).status_code == 201
    r = client.post("/auth/register", json=payload)
    assert r.status_code == 409


# ── /auth/login ───────────────────────────────────────────────────────────────


def test_login_sets_cookie_and_returns_token(client):
    client.post(
        "/auth/register", json={"email": "b@example.com", "password": "password1"}
    )
    # Clear cookie jar so we know the login response alone sets it.
    client.cookies.clear()
    r = client.post(
        "/auth/login",
        json={"email": "b@example.com", "password": "password1"},
    )
    assert r.status_code == 200
    assert r.cookies.get(AUTH_COOKIE_NAME) == r.json()["access_token"]


def test_login_invalid_credentials(client):
    client.post(
        "/auth/register", json={"email": "c@example.com", "password": "password1"}
    )
    r = client.post(
        "/auth/login",
        json={"email": "c@example.com", "password": "wrong"},
    )
    assert r.status_code == 401
    assert r.cookies.get(AUTH_COOKIE_NAME) is None


# ── /auth/me  (dual-mode auth) ─────────────────────────────────────────────────


def test_me_with_cookie(client):
    client.post(
        "/auth/register",
        json={"email": "d@example.com", "password": "password1"},
    )
    # TestClient persists cookies across requests in its jar.
    r = client.get("/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == "d@example.com"


def test_me_with_bearer_header(client):
    reg = client.post(
        "/auth/register",
        json={"email": "e@example.com", "password": "password1"},
    )
    token = reg.json()["access_token"]
    # Drop the cookie so only the header authenticates the request.
    client.cookies.clear()
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "e@example.com"


def test_me_unauthenticated(client):
    r = client.get("/auth/me")
    assert r.status_code == 401


def test_me_with_invalid_cookie(client):
    client.cookies.set(AUTH_COOKIE_NAME, "not-a-jwt")
    r = client.get("/auth/me")
    assert r.status_code == 401


# ── /auth/logout ──────────────────────────────────────────────────────────────


def test_logout_clears_cookie(client):
    client.post(
        "/auth/register",
        json={"email": "f@example.com", "password": "password1"},
    )
    assert client.get("/auth/me").status_code == 200

    r = client.post("/auth/logout")
    assert r.status_code == 204

    # delete_cookie sets Max-Age=0 / expired date; the TestClient jar should drop it.
    assert client.cookies.get(AUTH_COOKIE_NAME) in (None, "")
    assert client.get("/auth/me").status_code == 401
