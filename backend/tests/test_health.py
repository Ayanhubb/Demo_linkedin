from fastapi.testclient import TestClient

from app.api.routes import health as health_route
from app.core.config import get_settings
from app.main import _browser_origins, create_app


def test_health_ok_when_dependencies_respond(monkeypatch) -> None:
    monkeypatch.setattr(health_route, "postgres_ready", lambda: True)
    monkeypatch.setattr(health_route, "redis_ready", lambda: True)
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_reports_redis_outage(monkeypatch) -> None:
    monkeypatch.setattr(health_route, "postgres_ready", lambda: True)
    monkeypatch.setattr(health_route, "redis_ready", lambda: False)
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["dependency"] == "redis"


def test_loopback_hostname_is_also_allowed() -> None:
    assert _browser_origins("http://localhost:5173") == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    twin = _browser_origins(get_settings().frontend_origin)[-1]
    response = TestClient(create_app()).get("/health", headers={"Origin": twin})
    assert response.headers.get("access-control-allow-origin") == twin
