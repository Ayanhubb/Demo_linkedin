from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import LinkedInAccount, ScheduledPost, User
from app.db.models.scheduled_post import PostStatus
from app.main import create_app

ACCESS_TOKEN = "linkedin-access-token-do-not-store-plaintext"


def _connect_account(session: Session, member_id: str = "member123") -> LinkedInAccount:
    user = User(email=f"{uuid.uuid4()}@member.linkedin.local")
    session.add(user)
    session.flush()
    account = LinkedInAccount(user_id=user.id, linkedin_member_id=member_id)
    account.set_access_token(ACCESS_TOKEN)
    session.add(account)
    session.flush()
    return account


def _future(hours: int = 2) -> str:
    moment = datetime.now(timezone.utc) + timedelta(hours=hours)
    return moment.isoformat()


def _client(db_session: Session) -> TestClient:
    app = create_app()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def test_schedule_requires_connected_account(db_session: Session) -> None:
    client = _client(db_session)
    response = client.post(
        "/api/posts/schedule",
        json={"content": "Hello LinkedIn!", "scheduled_at": _future()},
    )
    assert response.status_code == 409


def test_schedule_validation(db_session: Session) -> None:
    _connect_account(db_session)
    client = _client(db_session)

    empty = client.post(
        "/api/posts/schedule",
        json={"content": "   ", "scheduled_at": _future()},
    )
    assert empty.status_code == 422

    too_long = client.post(
        "/api/posts/schedule",
        json={"content": "x" * 3001, "scheduled_at": _future()},
    )
    assert too_long.status_code == 422

    naive = client.post(
        "/api/posts/schedule",
        json={"content": "Hello", "scheduled_at": "2026-10-08T10:00:00"},
    )
    assert naive.status_code == 422

    past = client.post(
        "/api/posts/schedule",
        json={"content": "Hello", "scheduled_at": "2020-01-01T00:00:00+00:00"},
    )
    assert past.status_code == 422


def test_schedule_list_get_and_delete(db_session: Session) -> None:
    account = _connect_account(db_session)
    client = _client(db_session)
    sooner = datetime.now(timezone.utc) + timedelta(days=1)
    later = datetime.now(timezone.utc) + timedelta(days=3)
    offset = timezone(timedelta(hours=5, minutes=30))

    created_later = client.post(
        "/api/posts/schedule",
        json={"content": "Later post", "scheduled_at": later.isoformat()},
    )
    created_sooner = client.post(
        "/api/posts/schedule",
        json={"content": "Hello LinkedIn!", "scheduled_at": sooner.astimezone(offset).isoformat()},
    )
    assert created_later.status_code == 201
    assert created_sooner.status_code == 201
    body = created_sooner.json()
    assert body["content"] == "Hello LinkedIn!"
    assert body["status"] == "scheduled"
    returned_time = datetime.fromisoformat(body["scheduled_at"])
    assert returned_time.tzinfo is not None
    assert returned_time == sooner
    assert "created_at" in body
    assert ACCESS_TOKEN not in created_sooner.text
    post_id = body["id"]

    stored = db_session.scalar(select(ScheduledPost).where(ScheduledPost.id == uuid.UUID(post_id)))
    assert stored is not None
    assert stored.status is PostStatus.SCHEDULED
    assert stored.user_id == account.user_id
    assert stored.linkedin_account_id == account.id
    assert stored.idempotency_key is not None
    assert stored.scheduled_at.tzinfo is not None

    listing = client.get("/api/posts")
    assert listing.status_code == 200
    contents = [item["content"] for item in listing.json()]
    assert contents[0] == "Later post"
    assert "Hello LinkedIn!" in contents

    scheduled_only = client.get("/api/posts", params={"status": "scheduled"})
    assert scheduled_only.status_code == 200
    assert len(scheduled_only.json()) == 2
    invalid_filter = client.get("/api/posts", params={"status": "nope"})
    assert invalid_filter.status_code == 422

    fetched = client.get(f"/api/posts/{post_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == post_id
    missing = client.get(f"/api/posts/{uuid.uuid4()}")
    assert missing.status_code == 404

    deleted = client.delete(f"/api/posts/{created_later.json()['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/posts/{created_later.json()['id']}").status_code == 404

    published_id = uuid.uuid4()
    published = ScheduledPost(
        id=published_id,
        user_id=account.user_id,
        linkedin_account_id=account.id,
        content="Already live",
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=3),
        status=PostStatus.PUBLISHED,
        linkedin_post_id="urn:li:share:9",
        idempotency_key=uuid.uuid4(),
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(published)
    db_session.flush()
    removed = client.delete(f"/api/posts/{published_id}")
    assert removed.status_code == 204
    assert client.get(f"/api/posts/{published_id}").status_code == 404
