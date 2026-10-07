import logging

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.main import create_app
from app.services.linkedin.posts_service import TransientLinkedInPostError


def test_validation_error_is_a_safe_body() -> None:
    response = TestClient(create_app()).post("/api/posts/schedule", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_error"
    assert "Traceback" not in response.text
    assert "message" in body


def test_unexpected_error_hides_the_traceback(caplog) -> None:
    app = create_app()

    @app.get("/_test/boom")
    def boom() -> None:
        raise RuntimeError("access_token=super-secret-token")

    with caplog.at_level(logging.ERROR):
        response = TestClient(app, raise_server_exceptions=False).get("/_test/boom")
    assert response.status_code == 500
    assert response.json() == {"error": "internal_error", "message": "Unexpected server error"}
    assert "super-secret-token" not in response.text
    assert "Traceback" not in response.text
    assert "super-secret-token" not in caplog.text


def test_database_error_is_safe() -> None:
    app = create_app()

    @app.get("/_test/db")
    def broken() -> None:
        raise OperationalError("SELECT", {}, Exception("password=scheduler"))

    response = TestClient(app, raise_server_exceptions=False).get("/_test/db")
    assert response.status_code == 500
    assert response.json()["error"] == "database_error"
    assert "password" not in response.text
    assert "Traceback" not in response.text


def test_linkedin_transient_error_is_safe() -> None:
    app = create_app()

    @app.get("/_test/linkedin")
    def unavailable() -> None:
        raise TransientLinkedInPostError("rate_limited", http_status=429, metadata={})

    response = TestClient(app, raise_server_exceptions=False).get("/_test/linkedin")
    assert response.status_code == 503
    assert response.json() == {
        "error": "linkedin_api_error",
        "message": "LinkedIn temporarily unavailable",
    }
