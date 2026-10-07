from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.linkedin_auth import get_linkedin_client
from app.core.config import get_settings, reset_settings
from app.db.database import get_db
from app.db.models import LinkedInAccount, User
from app.main import create_app
from app.services.linkedin.client import LinkedInClient
from app.services.linkedin.oauth_service import STATE_COOKIE, InMemoryOAuthStateStore

CLIENT_SECRET = "test-client-secret-do-not-leak"
ACCESS_TOKEN = "linkedin-access-token-do-not-store-plaintext"
REFRESH_TOKEN = "linkedin-refresh-token-do-not-store-plaintext"
AUTH_CODE = "authorization-code-do-not-log"
REDIRECT_URI = "http://localhost:8000/api/auth/linkedin/callback"
FRONTEND = "http://localhost:5173"


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class FakeLinkedIn:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.bodies: list[str] = []
        self.token_status = 200
        self.token_body: dict[str, object] = {
            "access_token": ACCESS_TOKEN,
            "expires_in": 3600,
            "refresh_token": REFRESH_TOKEN,
            "scope": "openid,w_member_social",
        }
        self.userinfo_status = 200
        self.userinfo_body: dict[str, object] = {"sub": "member123", "name": "Ada Lovelace"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        self.bodies.append(request.content.decode())
        if request.url.path == "/oauth/v2/accessToken":
            return httpx.Response(self.token_status, json=self.token_body)
        if request.url.path == "/v2/userinfo":
            return httpx.Response(self.userinfo_status, json=self.userinfo_body)
        if request.url.path in {"/v2/me", "/oauth/v2/authorization"}:
            raise AssertionError(f"deprecated or unexpected call: {request.url.path}")
        return httpx.Response(404, json={"error": "not_found"})


@pytest.fixture
def oauth_client(db_session: Session):
    os.environ["LINKEDIN_CLIENT_ID"] = "test-client-id"
    os.environ["LINKEDIN_CLIENT_SECRET"] = CLIENT_SECRET
    os.environ["LINKEDIN_REDIRECT_URI"] = REDIRECT_URI
    os.environ["LINKEDIN_SCOPES"] = "w_member_social"
    os.environ["FRONTEND_ORIGIN"] = FRONTEND
    reset_settings()
    clock = MutableClock()
    linkedin = FakeLinkedIn()
    app = create_app(state_store=InMemoryOAuthStateStore(ttl_seconds=600, clock=clock))

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_linkedin_client] = lambda: LinkedInClient(
        get_settings(),
        transport=httpx.MockTransport(linkedin.handler),
    )
    with TestClient(app) as client:
        yield client, linkedin, clock, db_session
    reset_settings()


def _location_params(response: httpx.Response) -> dict[str, list[str]]:
    return parse_qs(urlsplit(response.headers["location"]).query)


def _start(client: TestClient) -> str:
    response = client.get("/api/auth/linkedin", follow_redirects=False)
    assert response.status_code == 302
    return _location_params(response)["state"][0]


def test_authorization_url(oauth_client) -> None:
    client, linkedin, _clock, _db = oauth_client
    response = client.get("/api/auth/linkedin", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    parts = urlsplit(location)
    params = parse_qs(parts.query)
    assert parts.scheme == "https"
    assert parts.netloc == "www.linkedin.com"
    assert parts.path == "/oauth/v2/authorization"
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["test-client-id"]
    assert params["redirect_uri"] == [REDIRECT_URI]
    assert params["scope"] == ["openid w_member_social"]
    assert "r_liteprofile" not in location
    assert len(params["state"][0]) >= 43
    assert "client_secret" not in location
    assert CLIENT_SECRET not in location
    assert "httponly" in response.headers["set-cookie"].lower()
    assert linkedin.calls == []


def test_callback_state_validation(oauth_client) -> None:
    client, linkedin, _clock, _db = oauth_client
    _start(client)
    mismatched = client.get(
        "/api/auth/linkedin/callback",
        params={"code": AUTH_CODE, "state": "a" * 43},
        follow_redirects=False,
    )
    assert mismatched.status_code == 302
    assert _location_params(mismatched)["linkedin_error"] == ["invalid_state"]
    assert linkedin.calls == []

    state = _start(client)
    first = client.get(
        "/api/auth/linkedin/callback",
        params={"code": AUTH_CODE, "state": state},
        follow_redirects=False,
    )
    assert _location_params(first)["linkedin"] == ["connected"]
    replay = client.get(
        "/api/auth/linkedin/callback",
        params={"code": AUTH_CODE, "state": state},
        follow_redirects=False,
    )
    assert _location_params(replay)["linkedin_error"] == ["invalid_state"]


def test_successful_token_exchange(oauth_client) -> None:
    client, linkedin, _clock, db_session = oauth_client
    state = _start(client)

    response = client.get(
        "/api/auth/linkedin/callback",
        params={"code": AUTH_CODE, "state": state},
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(f"{FRONTEND}?")
    assert _location_params(response)["linkedin"] == ["connected"]
    assert ACCESS_TOKEN not in location
    assert REFRESH_TOKEN not in location
    assert CLIENT_SECRET not in location
    assert AUTH_CODE not in location
    assert linkedin.calls == ["/oauth/v2/accessToken", "/v2/userinfo"]
    assert "grant_type=authorization_code" in linkedin.bodies[0]
    assert f"redirect_uri={REDIRECT_URI.replace(':', '%3A').replace('/', '%2F')}" in linkedin.bodies[0] or (
        REDIRECT_URI in linkedin.bodies[0]
    )
    assert CLIENT_SECRET in linkedin.bodies[0]

    account = db_session.scalar(select(LinkedInAccount))
    assert account is not None
    assert account.linkedin_member_id == "member123"
    assert account.read_access_token() == ACCESS_TOKEN
    assert account.read_refresh_token() == REFRESH_TOKEN
    assert ACCESS_TOKEN not in account.access_token_encrypted
    assert REFRESH_TOKEN not in (account.refresh_token_encrypted or "")
    assert account.token_expires_at is not None
    assert account.token_expires_at.tzinfo is not None
    assert db_session.scalar(select(User)) is not None


def test_linkedin_oauth_failure(oauth_client, caplog: pytest.LogCaptureFixture) -> None:
    client, linkedin, _clock, db_session = oauth_client
    state = _start(client)
    linkedin.token_status = 400
    linkedin.token_body = {"error": "invalid_grant", "error_description": "expired authorization code"}

    with caplog.at_level("INFO"):
        failed = client.get(
            "/api/auth/linkedin/callback",
            params={"code": AUTH_CODE, "state": state},
            follow_redirects=False,
        )
    assert _location_params(failed)["linkedin_error"] == ["token_exchange_failed"]
    assert "expired authorization code" not in failed.headers["location"]
    assert ACCESS_TOKEN not in failed.headers["location"]
    assert CLIENT_SECRET not in caplog.text
    assert AUTH_CODE not in caplog.text
    assert db_session.scalar(select(LinkedInAccount)) is None

    denied_state = _start(client)
    denied = client.get(
        "/api/auth/linkedin/callback",
        params={"error": "access_denied", "error_description": "user cancelled", "state": denied_state},
        follow_redirects=False,
    )
    assert _location_params(denied)["linkedin_error"] == ["access_denied"]
    assert "user cancelled" not in denied.headers["location"]
    assert linkedin.calls == ["/oauth/v2/accessToken"]


def test_malformed_callback(oauth_client) -> None:
    client, linkedin, _clock, _db = oauth_client
    missing = client.get("/api/auth/linkedin/callback", follow_redirects=False)
    blank_code = client.get(
        "/api/auth/linkedin/callback",
        params={"code": "  ", "state": "a" * 43},
        follow_redirects=False,
    )
    illegal_state = client.get(
        "/api/auth/linkedin/callback",
        params={"code": AUTH_CODE, "state": "not a state"},
        follow_redirects=False,
    )

    assert _location_params(missing)["linkedin_error"] == ["malformed_callback"]
    assert _location_params(blank_code)["linkedin_error"] == ["malformed_callback"]
    assert _location_params(illegal_state)["linkedin_error"] == ["malformed_callback"]
    assert linkedin.calls == []


def test_expired_and_invalid_state(oauth_client) -> None:
    client, linkedin, clock, _db = oauth_client
    unknown = "c" * 43
    client.cookies.set(STATE_COOKIE, unknown, path="/api/auth/linkedin/callback")
    unknown_response = client.get(
        "/api/auth/linkedin/callback",
        params={"code": AUTH_CODE, "state": unknown},
        follow_redirects=False,
    )
    assert _location_params(unknown_response)["linkedin_error"] == ["invalid_state"]

    state = _start(client)
    clock.advance(601)
    expired = client.get(
        "/api/auth/linkedin/callback",
        params={"code": AUTH_CODE, "state": state},
        follow_redirects=False,
    )
    assert _location_params(expired)["linkedin_error"] == ["expired_state"]
    assert linkedin.calls == []
    assert ACCESS_TOKEN not in expired.headers["location"]
    assert CLIENT_SECRET not in expired.headers["location"]
