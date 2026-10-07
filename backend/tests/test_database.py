from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DataError, IntegrityError, StatementError

from app.core.security import TokenStorageError
from app.db.database import get_db, session_scope
from app.db.models import LinkedInAccount, PostStatus, ScheduledPost, User

ACCESS_TOKEN = "linkedin-access-token-do-not-store-plaintext"
REFRESH_TOKEN = "linkedin-refresh-token-do-not-store-plaintext"


def _user(session, email: str | None = None) -> User:
    user = User(email=email or f"{uuid.uuid4()}@example.com")
    session.add(user)
    session.flush()
    return user


def _account(session, user: User, member_id: str = "member-1") -> LinkedInAccount:
    account = LinkedInAccount(
        user_id=user.id,
        linkedin_member_id=member_id,
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    account.set_access_token(ACCESS_TOKEN)
    account.set_refresh_token(REFRESH_TOKEN)
    session.add(account)
    session.flush()
    return account


def _post(session, user: User, account: LinkedInAccount, **overrides) -> ScheduledPost:
    values = {
        "user_id": user.id,
        "linkedin_account_id": account.id,
        "content": "Scheduled hello",
        "scheduled_at": datetime.now(timezone.utc) + timedelta(hours=2),
        "idempotency_key": uuid.uuid4(),
    }
    values.update(overrides)
    post = ScheduledPost(**values)
    session.add(post)
    session.flush()
    return post


def test_persists_user_account_and_post(db_session) -> None:
    user = _user(db_session)
    account = _account(db_session, user)
    post = _post(db_session, user, account)
    db_session.commit()

    stored = db_session.get(ScheduledPost, post.id)
    assert stored is not None
    assert stored.status is PostStatus.SCHEDULED
    assert stored.attempt_count == 0
    assert stored.max_attempts == 5
    assert stored.linkedin_post_id is None
    assert stored.last_error is None
    assert stored.user_id == user.id
    assert stored.linkedin_account_id == account.id
    assert account.read_access_token() == ACCESS_TOKEN
    assert account.read_refresh_token() == REFRESH_TOKEN
    assert account.access_token_encrypted != ACCESS_TOKEN
    assert ACCESS_TOKEN not in account.access_token_encrypted
    assert REFRESH_TOKEN not in (account.refresh_token_encrypted or "")
    assert ACCESS_TOKEN not in repr(account)
    assert account.access_token_encrypted not in repr(account)


def test_timestamps_are_timezone_aware(db_session) -> None:
    user = _user(db_session)
    account = _account(db_session, user)
    published_at = datetime.now(timezone.utc)
    post = _post(
        db_session,
        user,
        account,
        status=PostStatus.PUBLISHED,
        linkedin_post_id="urn:li:share:1",
        published_at=published_at,
        processing_started_at=published_at,
        next_retry_at=None,
    )
    db_session.commit()

    assert user.created_at.tzinfo is not None
    assert user.updated_at.tzinfo is not None
    assert account.token_expires_at is not None
    assert account.token_expires_at.tzinfo is not None
    assert post.scheduled_at.tzinfo is not None
    assert post.published_at is not None and post.published_at.tzinfo is not None
    assert post.processing_started_at is not None
    assert post.processing_started_at.tzinfo is not None

    column_types = set(
        db_session.execute(
            text(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND column_name IN (
                    'created_at', 'updated_at', 'token_expires_at', 'scheduled_at',
                    'next_retry_at', 'processing_started_at', 'published_at'
                  )
                """
            )
        ).scalars()
    )
    assert column_types == {"timestamp with time zone"}


def test_naive_datetime_is_rejected(db_session) -> None:
    user = _user(db_session)
    account = _account(db_session, user)
    with pytest.raises(StatementError, match="timezone-aware"):
        _post(db_session, user, account, scheduled_at=datetime(2026, 10, 7, 12, 0, 0))


def test_plaintext_token_cannot_be_stored(db_session) -> None:
    user = _user(db_session)
    with pytest.raises(TokenStorageError, match="encrypted"):
        LinkedInAccount(
            user_id=user.id,
            linkedin_member_id="member-plain",
            access_token_encrypted=ACCESS_TOKEN,
        )


def test_refresh_token_is_optional(db_session) -> None:
    user = _user(db_session)
    account = LinkedInAccount(user_id=user.id, linkedin_member_id="member-no-refresh")
    account.set_access_token(ACCESS_TOKEN)
    db_session.add(account)
    db_session.commit()
    assert account.refresh_token_encrypted is None
    assert account.read_refresh_token() is None


def test_duplicate_idempotency_key_is_rejected(db_session) -> None:
    user = _user(db_session)
    account = _account(db_session, user)
    key = uuid.uuid4()
    _post(db_session, user, account, idempotency_key=key)
    db_session.commit()

    with pytest.raises(IntegrityError):
        _post(db_session, user, account, idempotency_key=key, content="Second copy")
    db_session.rollback()


def test_foreign_keys_are_enforced(db_session) -> None:
    missing_user = uuid.uuid4()
    account = LinkedInAccount(user_id=missing_user, linkedin_member_id="orphan")
    account.set_access_token(ACCESS_TOKEN)
    db_session.add(account)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    user = _user(db_session)
    other = _user(db_session)
    account = _account(db_session, user, member_id="owned-by-first")
    with pytest.raises(IntegrityError):
        _post(db_session, other, account)
    db_session.rollback()


def test_user_with_account_cannot_be_deleted(db_session) -> None:
    user = _user(db_session)
    _account(db_session, user)
    db_session.commit()
    db_session.delete(user)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_invalid_status_and_content_are_rejected(db_session) -> None:
    user = _user(db_session)
    account = _account(db_session, user)
    db_session.commit()

    with pytest.raises(DataError):
        db_session.execute(
            text(
                """
                INSERT INTO scheduled_posts (
                    user_id, linkedin_account_id, content, scheduled_at, status, idempotency_key
                ) VALUES (
                    :user_id, :account_id, 'hello', now(), 'RETRY', gen_random_uuid()
                )
                """
            ),
            {"user_id": user.id, "account_id": account.id},
        )
    db_session.rollback()

    with pytest.raises(IntegrityError):
        _post(db_session, user, account, content="")
    db_session.rollback()

    with pytest.raises(IntegrityError):
        _post(db_session, user, account, content="x" * 3001)
    db_session.rollback()

    with pytest.raises(IntegrityError):
        _post(db_session, user, account, attempt_count=-1)
    db_session.rollback()


def test_required_indexes_and_revision(db_session) -> None:
    index_rows = db_session.execute(
        text(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'scheduled_posts'
            """
        )
    ).all()
    indexes = {row.indexname: row.indexdef for row in index_rows}
    assert "ix_scheduled_posts_status_scheduled_at" in indexes
    assert "ix_scheduled_posts_linkedin_account_id" in indexes
    assert "ix_scheduled_posts_idempotency_key" in indexes
    assert "UNIQUE" in indexes["ix_scheduled_posts_idempotency_key"].upper()

    foreign_keys = set(
        db_session.execute(
            text(
                """
                SELECT conname
                FROM pg_constraint
                WHERE contype = 'f'
                """
            )
        ).scalars()
    )
    assert "fk_linkedin_accounts_user_id_users" in foreign_keys
    assert "fk_scheduled_posts_user_id_users" in foreign_keys
    assert "fk_scheduled_posts_linkedin_account_id_linkedin_accounts" in foreign_keys
    assert "fk_scheduled_posts_account_same_user" in foreign_keys

    revision = db_session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "0001_initial"


def test_updated_at_changes_when_row_changes(db_session) -> None:
    user = _user(db_session)
    db_session.commit()
    original = user.updated_at - timedelta(days=1)
    user.updated_at = original
    user.email = f"{uuid.uuid4()}@example.com"
    db_session.commit()
    assert user.updated_at > original
    assert user.created_at < user.updated_at


def test_session_scope_commits_and_rolls_back(migrated_database: None) -> None:
    del migrated_database
    email = f"{uuid.uuid4()}@example.com"
    with session_scope() as session:
        session.add(User(email=email))

    with session_scope() as session:
        found = session.scalar(select(User).where(User.email == email))
        assert found is not None

    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(User(email=email))

    failed_email = f"{uuid.uuid4()}@example.com"
    with pytest.raises(RuntimeError):
        with session_scope() as session:
            session.add(User(email=failed_email))
            raise RuntimeError("fail before commit")

    with session_scope() as session:
        assert session.scalar(select(User).where(User.email == failed_email)) is None
        stored = session.scalar(select(User).where(User.email == email))
        assert stored is not None
        session.delete(stored)


def test_get_db_closes_and_commits(migrated_database: None) -> None:
    del migrated_database
    email = f"{uuid.uuid4()}@example.com"
    generator = get_db()
    session = next(generator)
    session.add(User(email=email))
    with pytest.raises(StopIteration):
        next(generator)

    with session_scope() as cleanup:
        stored = cleanup.scalar(select(User).where(User.email == email))
        assert stored is not None
        cleanup.delete(stored)


def test_status_transitions_follow_the_lifecycle(db_session) -> None:
    user = _user(db_session)
    account = _account(db_session, user)
    post = _post(db_session, user, account)
    assert post.status is PostStatus.SCHEDULED

    post.status = PostStatus.PROCESSING
    post.attempt_count = 1
    db_session.flush()
    assert db_session.get(ScheduledPost, post.id).status is PostStatus.PROCESSING

    post.status = PostStatus.PUBLISHED
    post.linkedin_post_id = "urn:li:share:1"
    db_session.flush()
    stored = db_session.get(ScheduledPost, post.id)
    assert stored.status is PostStatus.PUBLISHED
    assert stored.linkedin_post_id == "urn:li:share:1"
