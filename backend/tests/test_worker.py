from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import get_session_factory, session_scope
from app.db.models import LinkedInAccount, ScheduledPost, User
from app.db.models.scheduled_post import PostStatus
from app.services.linkedin.client import LinkedInClient
from app.workers.celery_app import celery_app
from app.workers.tasks import (
    MAX_RETRIES,
    claim_due_posts,
    process_due_posts,
    process_due_posts_impl,
    publish_claimed_post,
    retry_delay_seconds,
)

ACCESS_TOKEN = "linkedin-access-token-do-not-store-plaintext"


def _account_and_post(
    session: Session,
    *,
    scheduled_at: datetime,
    status: PostStatus = PostStatus.SCHEDULED,
    next_retry_at: datetime | None = None,
    content: str = "Hello LinkedIn!",
) -> ScheduledPost:
    user = User(email=f"{uuid.uuid4()}@member.linkedin.local")
    session.add(user)
    session.flush()
    account = LinkedInAccount(user_id=user.id, linkedin_member_id=f"member-{uuid.uuid4().hex}")
    account.set_access_token(ACCESS_TOKEN)
    session.add(account)
    session.flush()
    post = ScheduledPost(
        user_id=user.id,
        linkedin_account_id=account.id,
        content=content,
        scheduled_at=scheduled_at,
        status=status,
        idempotency_key=uuid.uuid4(),
        next_retry_at=next_retry_at,
    )
    session.add(post)
    session.flush()
    return post


def _cleanup(post_ids: list[uuid.UUID]) -> None:
    with session_scope() as session:
        posts = list(session.scalars(select(ScheduledPost).where(ScheduledPost.id.in_(post_ids))).all())
        user_ids = {post.user_id for post in posts}
        account_ids = {post.linkedin_account_id for post in posts}
        for post in posts:
            session.delete(post)
        session.flush()
        accounts = list(session.scalars(select(LinkedInAccount).where(LinkedInAccount.id.in_(account_ids))).all())
        for account in accounts:
            session.delete(account)
        session.flush()
        users = list(session.scalars(select(User).where(User.id.in_(user_ids))).all())
        for user in users:
            session.delete(user)


def _mock_client(status: int, *, post_id: str | None = "urn:li:share:55", body: dict | None = None) -> tuple[LinkedInClient, list[int]]:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(status)
        headers = {"x-restli-id": post_id} if post_id and status == 201 else {}
        return httpx.Response(status, headers=headers, json=body or {})

    return LinkedInClient(get_settings(), transport=httpx.MockTransport(handler)), calls


def test_due_post_discovery(db_session: Session) -> None:
    now = datetime.now(timezone.utc)
    due = _account_and_post(db_session, scheduled_at=now - timedelta(minutes=5))
    future = _account_and_post(db_session, scheduled_at=now + timedelta(hours=2))
    waiting = _account_and_post(
        db_session,
        scheduled_at=now - timedelta(minutes=5),
        next_retry_at=now + timedelta(minutes=10),
    )
    processing = _account_and_post(
        db_session,
        scheduled_at=now - timedelta(minutes=5),
        status=PostStatus.PROCESSING,
    )

    claimed = claim_due_posts(db_session, now=now)

    assert claimed == [due.id]
    stored = db_session.get(ScheduledPost, due.id)
    assert stored is not None
    assert stored.status is PostStatus.PROCESSING
    assert stored.attempt_count == 1
    assert stored.processing_started_at is not None
    assert stored.processing_started_at.tzinfo is not None
    assert db_session.get(ScheduledPost, future.id).status is PostStatus.SCHEDULED
    assert db_session.get(ScheduledPost, waiting.id).status is PostStatus.SCHEDULED
    assert db_session.get(ScheduledPost, processing.id).status is PostStatus.PROCESSING
    assert processing.id not in claimed


def test_row_locking_skips_a_locked_post() -> None:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        post = _account_and_post(session, scheduled_at=now - timedelta(minutes=1))
        post_id = post.id
    holder = get_session_factory()()
    other = get_session_factory()()
    try:
        other.execute(text("SET lock_timeout = '2s'"))
        held = claim_due_posts(holder, now=now)
        skipped = claim_due_posts(other, now=now)
        assert held == [post_id]
        assert skipped == []
        holder.commit()
        other.rollback()
        with session_scope() as session:
            stored = session.get(ScheduledPost, post_id)
            assert stored is not None
            assert stored.status is PostStatus.PROCESSING
            assert stored.attempt_count == 1
    finally:
        holder.close()
        other.close()
        _cleanup([post_id])


def test_successful_processing() -> None:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        post = _account_and_post(session, scheduled_at=now - timedelta(minutes=1))
        post_id = post.id
    client, calls = _mock_client(201, post_id="urn:li:share:55")
    try:
        result = process_due_posts_impl(client=client)
        assert result == {"claimed": 1}
        assert calls == [201]
        with session_scope() as session:
            stored = session.get(ScheduledPost, post_id)
            assert stored is not None
            assert stored.status is PostStatus.PUBLISHED
            assert stored.linkedin_post_id == "urn:li:share:55"
            assert stored.published_at is not None
            assert stored.published_at.tzinfo is not None
            assert stored.attempt_count == 1
            assert stored.last_error is None
            assert stored.updated_at is not None
            assert stored.updated_at.tzinfo is not None
    finally:
        _cleanup([post_id])


def test_permanent_failure_marks_the_post_failed() -> None:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        post = _account_and_post(session, scheduled_at=now - timedelta(minutes=1))
        post_id = post.id
    client, _calls = _mock_client(401, body={"message": ACCESS_TOKEN})
    try:
        process_due_posts_impl(client=client)
        with session_scope() as session:
            stored = session.get(ScheduledPost, post_id)
            assert stored is not None
            assert stored.status is PostStatus.FAILED
            assert stored.last_error == "invalid_token"
            assert stored.linkedin_post_id is None
            assert stored.published_at is None
            assert stored.attempt_count == 1
            assert ACCESS_TOKEN not in stored.last_error
    finally:
        _cleanup([post_id])


def test_concurrent_workers_publish_once() -> None:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        post = _account_and_post(session, scheduled_at=now - timedelta(minutes=1))
        post_id = post.id
    calls: list[int] = []
    lock = threading.Lock()

    def handler(_request: httpx.Request) -> httpx.Response:
        with lock:
            calls.append(1)
        return httpx.Response(201, headers={"x-restli-id": "urn:li:share:77"})

    client = LinkedInClient(get_settings(), transport=httpx.MockTransport(handler))
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            barrier.wait(timeout=5)
            process_due_posts_impl(client=client)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run), threading.Thread(target=run)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert errors == []
        assert not any(thread.is_alive() for thread in threads)
        assert calls == [1]
        with session_scope() as session:
            stored = session.get(ScheduledPost, post_id)
            assert stored is not None
            assert stored.status is PostStatus.PUBLISHED
            assert stored.linkedin_post_id == "urn:li:share:77"
            assert stored.attempt_count == 1
    finally:
        _cleanup([post_id])


def test_beat_schedule_uses_utc() -> None:
    schedule = celery_app.conf.beat_schedule["process-due-posts"]
    assert schedule["task"] == "app.workers.tasks.process_due_posts"
    assert "app.workers.tasks.process_due_posts" in celery_app.tasks
    assert celery_app.conf.timezone == "UTC"
    assert celery_app.conf.enable_utc is True
    assert celery_app.conf.broker_url.startswith("redis://")
    assert process_due_posts.max_retries == 0


def _counting_client(responses: list[tuple[int, str | None]]) -> tuple[LinkedInClient, list[int]]:
    calls: list[int] = []
    lock = threading.Lock()
    pending = list(responses)

    def handler(_request: httpx.Request) -> httpx.Response:
        with lock:
            status, post_id = pending.pop(0) if pending else (201, "urn:li:share:extra")
            calls.append(status)
        headers = {"x-restli-id": post_id} if post_id and status == 201 else {}
        return httpx.Response(status, headers=headers)

    return LinkedInClient(get_settings(), transport=httpx.MockTransport(handler)), calls


def _make_due_post() -> uuid.UUID:
    with session_scope() as session:
        post = _account_and_post(
            session,
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        return post.id


def _open_retry(post_id: uuid.UUID) -> bool:
    with session_scope() as session:
        stored = session.get(ScheduledPost, post_id)
        assert stored is not None
        if stored.status is not PostStatus.SCHEDULED:
            return False
        stored.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        return True


def test_backoff_is_exponential_with_jitter() -> None:
    centers = {1: 5, 2: 15, 3: 45, 4: 120}
    assert MAX_RETRIES == 4
    for attempt, center in centers.items():
        samples = [retry_delay_seconds(attempt) for _ in range(24)]
        assert all(center * 0.8 <= sample <= center * 1.2 for sample in samples)
        assert len({round(sample, 5) for sample in samples}) > 1


def test_transient_retry_then_success_creates_one_linkedin_post() -> None:
    post_id = _make_due_post()
    client, calls = _counting_client([(429, None), (201, "urn:li:share:retry-once")])
    try:
        process_due_posts_impl(client=client)
        with session_scope() as session:
            waiting = session.get(ScheduledPost, post_id)
            assert waiting is not None
            key = waiting.idempotency_key
            assert waiting.status is PostStatus.SCHEDULED
            assert waiting.attempt_count == 1
            assert waiting.last_error == "rate_limited"
            assert waiting.linkedin_post_id is None
            assert waiting.next_retry_at is not None
            remaining = (waiting.next_retry_at - datetime.now(timezone.utc)).total_seconds()
            assert 3 <= remaining <= 7
        _open_retry(post_id)
        process_due_posts_impl(client=client)
        process_due_posts_impl(client=client)
        publish_claimed_post(post_id, client=client)
        assert calls == [429, 201]
        with session_scope() as session:
            stored = session.get(ScheduledPost, post_id)
            assert stored is not None
            assert stored.status is PostStatus.PUBLISHED
            assert stored.linkedin_post_id == "urn:li:share:retry-once"
            assert stored.idempotency_key == key
            assert stored.attempt_count == 2
            assert stored.next_retry_at is None
    finally:
        _cleanup([post_id])


def test_four_transient_failures_become_failed() -> None:
    post_id = _make_due_post()
    client, calls = _counting_client([(500, None)] * (MAX_RETRIES + 1))
    try:
        for _ in range(MAX_RETRIES + 1):
            process_due_posts_impl(client=client)
            if not _open_retry(post_id):
                break
        assert calls == [500, 500, 500, 500, 500]
        with session_scope() as session:
            stored = session.get(ScheduledPost, post_id)
            assert stored is not None
            assert stored.status is PostStatus.FAILED
            assert stored.attempt_count == MAX_RETRIES + 1
            assert stored.last_error == "server_error"
            assert stored.linkedin_post_id is None
            assert stored.next_retry_at is None
    finally:
        _cleanup([post_id])


def test_timeout_and_connection_failure_are_retried() -> None:
    post_id = _make_due_post()
    calls: list[str] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append("sent")
        if len(calls) == 1:
            raise httpx.TimeoutException("timed out")
        raise httpx.ConnectError("temporary connection failure")

    client = LinkedInClient(get_settings(), transport=httpx.MockTransport(handler))
    try:
        process_due_posts_impl(client=client)
        with session_scope() as session:
            stored = session.get(ScheduledPost, post_id)
            assert stored is not None
            assert stored.status is PostStatus.SCHEDULED
            assert stored.last_error == "timeout"
        _open_retry(post_id)
        process_due_posts_impl(client=client)
        with session_scope() as session:
            stored = session.get(ScheduledPost, post_id)
            assert stored is not None
            assert stored.status is PostStatus.SCHEDULED
            assert stored.last_error == "connection_failed"
            assert stored.linkedin_post_id is None
            assert stored.attempt_count == 2
    finally:
        _cleanup([post_id])


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "invalid_payload"), (401, "invalid_token"), (403, "invalid_permission")],
)
def test_permanent_client_errors_are_not_retried(status: int, code: str) -> None:
    post_id = _make_due_post()
    client, calls = _counting_client([(status, None), (201, "urn:li:share:should-not-exist")])
    try:
        process_due_posts_impl(client=client)
        process_due_posts_impl(client=client)
        assert calls == [status]
        with session_scope() as session:
            stored = session.get(ScheduledPost, post_id)
            assert stored is not None
            assert stored.status is PostStatus.FAILED
            assert stored.last_error == code
            assert stored.linkedin_post_id is None
            assert stored.next_retry_at is None
            assert stored.attempt_count == 1
    finally:
        _cleanup([post_id])


def test_scenario_a_worker_publishes_once() -> None:
    post_id = _make_due_post()
    client, calls = _counting_client([(201, "urn:li:share:scenario-a")])
    try:
        assert process_due_posts_impl(client=client) == {"claimed": 1}
        assert calls == [201]
        with session_scope() as session:
            stored = session.get(ScheduledPost, post_id)
            assert stored is not None
            assert stored.status is PostStatus.PUBLISHED
            assert stored.linkedin_post_id == "urn:li:share:scenario-a"
    finally:
        _cleanup([post_id])


def test_scenario_b_successful_task_retry_does_not_publish_again() -> None:
    post_id = _make_due_post()
    client, calls = _counting_client([(201, "urn:li:share:scenario-b"), (201, "urn:li:share:duplicate")])
    try:
        process_due_posts_impl(client=client)
        process_due_posts_impl(client=client)
        publish_claimed_post(post_id, client=client)
        assert calls == [201]
        with session_scope() as session:
            stored = session.get(ScheduledPost, post_id)
            assert stored is not None
            assert stored.linkedin_post_id == "urn:li:share:scenario-b"
            assert stored.status is PostStatus.PUBLISHED
    finally:
        _cleanup([post_id])


def test_scenario_c_two_workers_receive_the_same_task() -> None:
    _assert_one_publish_from_overlapping_calls("urn:li:share:scenario-c")


def test_scenario_d_two_scheduler_cycles_discover_the_same_post() -> None:
    post_id = _make_due_post()
    client, calls = _counting_client([(201, "urn:li:share:scenario-d"), (201, "urn:li:share:duplicate")])
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            barrier.wait(timeout=5)
            process_due_posts_impl(client=client)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run), threading.Thread(target=run)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert errors == []
        process_due_posts_impl(client=client)
        assert calls == [201]
        with session_scope() as session:
            stored = session.get(ScheduledPost, post_id)
            assert stored is not None
            assert stored.status is PostStatus.PUBLISHED
            assert stored.linkedin_post_id == "urn:li:share:scenario-d"
            assert stored.attempt_count == 1
    finally:
        _cleanup([post_id])


def test_scenario_e_publish_transaction_race_creates_one_post() -> None:
    with session_scope() as session:
        post = _account_and_post(
            session,
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            status=PostStatus.PROCESSING,
        )
        post.attempt_count = 1
        post_id = post.id
    calls: list[int] = []
    lock = threading.Lock()
    entered = threading.Barrier(2)

    def handler(_request: httpx.Request) -> httpx.Response:
        with lock:
            calls.append(1)
        return httpx.Response(201, headers={"x-restli-id": "urn:li:share:scenario-e"})

    client = LinkedInClient(get_settings(), transport=httpx.MockTransport(handler))
    errors: list[BaseException] = []

    def run() -> None:
        try:
            entered.wait(timeout=5)
            publish_claimed_post(post_id, client=client)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run), threading.Thread(target=run)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert errors == []
        assert calls == [1]
        with session_scope() as session:
            stored = session.get(ScheduledPost, post_id)
            assert stored is not None
            assert stored.status is PostStatus.PUBLISHED
            assert stored.linkedin_post_id == "urn:li:share:scenario-e"
    finally:
        _cleanup([post_id])


def _assert_one_publish_from_overlapping_calls(linkedin_post_id: str) -> None:
    post_id = _make_due_post()
    calls: list[int] = []
    lock = threading.Lock()

    def handler(_request: httpx.Request) -> httpx.Response:
        with lock:
            calls.append(1)
        return httpx.Response(201, headers={"x-restli-id": linkedin_post_id})

    client = LinkedInClient(get_settings(), transport=httpx.MockTransport(handler))
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            barrier.wait(timeout=5)
            process_due_posts_impl(client=client)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run), threading.Thread(target=run)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert errors == []
        assert calls == [1]
        with session_scope() as session:
            stored = session.get(ScheduledPost, post_id)
            assert stored is not None
            assert stored.status is PostStatus.PUBLISHED
            assert stored.linkedin_post_id == linkedin_post_id
    finally:
        _cleanup([post_id])
