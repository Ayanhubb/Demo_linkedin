from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.models.scheduled_post import PostStatus
from tests.test_schedule_api import _client, _connect_account, _future


def test_dashboard_without_account(db_session: Session) -> None:
    client = _client(db_session)
    status = client.get("/api/linkedin/status")
    dashboard = client.get("/api/dashboard")
    assert status.status_code == 200
    assert status.json() == {"connected": False}
    assert dashboard.json() == {
        "linkedin_connected": False,
        "scheduled_count": 0,
        "published_count": 0,
        "failed_count": 0,
    }


def test_dashboard_counts_posts_for_the_connected_account(db_session: Session) -> None:
    account = _connect_account(db_session)
    _insert(db_session, account, PostStatus.SCHEDULED)
    _insert(db_session, account, PostStatus.SCHEDULED)
    _insert(db_session, account, PostStatus.PUBLISHED)
    _insert(db_session, account, PostStatus.FAILED, last_error="invalid_token")
    _insert(db_session, account, PostStatus.PROCESSING)

    client = _client(db_session)
    body = client.get("/api/dashboard").json()
    assert body["linkedin_connected"] is True
    assert body["scheduled_count"] == 2
    assert body["published_count"] == 1
    assert body["failed_count"] == 1

    created = client.post("/api/posts/schedule", json={"content": "Hello", "scheduled_at": _future()})
    assert created.status_code == 201
    assert created.json()["attempt_count"] == 0
    assert created.json()["last_error"] is None


def _insert(session: Session, account, status: PostStatus, last_error: str | None = None) -> None:
    from app.db.models import ScheduledPost
    import uuid

    session.add(
        ScheduledPost(
            user_id=account.user_id,
            linkedin_account_id=account.id,
            content=f"Post {status.value}",
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=3),
            status=status,
            idempotency_key=uuid.uuid4(),
            last_error=last_error,
            attempt_count=1 if status is not PostStatus.SCHEDULED else 0,
        )
    )
    session.flush()
