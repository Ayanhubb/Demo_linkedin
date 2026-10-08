"""Create and query scheduled posts. Publishing is a later worker step."""

import secrets
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import LinkedInAccount, ScheduledPost
from app.db.models.scheduled_post import PostStatus


class NoLinkedInAccountError(Exception):
    """The operator has not connected a LinkedIn account."""


class ScheduledPostNotFoundError(Exception):
    """The post does not exist for the connected account."""


class ScheduledPostNotDeletableError(Exception):
    """The post was already accepted by LinkedIn."""


def create_scheduled_post(
    session: Session,
    content: str,
    scheduled_at: datetime,
    *,
    image_bytes: bytes | None = None,
    image_content_type: str | None = None,
) -> ScheduledPost:
    account = _connected_account(session)
    post = ScheduledPost(
        user_id=account.user_id,
        linkedin_account_id=account.id,
        content=content,
        image_bytes=image_bytes,
        image_content_type=image_content_type,
        scheduled_at=scheduled_at,
        status=PostStatus.SCHEDULED,
        idempotency_key=_new_idempotency_key(),
    )
    session.add(post)
    session.flush()
    return post


def list_scheduled_posts(session: Session, status: PostStatus | None = None) -> list[ScheduledPost]:
    account = _connected_account(session)
    query = select(ScheduledPost).where(ScheduledPost.user_id == account.user_id)
    if status is not None:
        query = query.where(ScheduledPost.status == status)
    query = query.order_by(ScheduledPost.scheduled_at.desc())
    return list(session.scalars(query).all())


def get_scheduled_post(session: Session, post_id: uuid.UUID) -> ScheduledPost:
    account = _connected_account(session)
    post = session.scalar(
        select(ScheduledPost).where(
            ScheduledPost.id == post_id,
            ScheduledPost.user_id == account.user_id,
        )
    )
    if post is None:
        raise ScheduledPostNotFoundError()
    return post


def delete_scheduled_post(session: Session, post_id: uuid.UUID) -> None:
    post = get_scheduled_post(session, post_id)
    session.delete(post)
    session.flush()


def linkedin_is_connected(session: Session) -> bool:
    return session.scalar(select(LinkedInAccount.id).limit(1)) is not None


def post_counts(session: Session) -> dict[str, int]:
    """Counts for the dashboard. Missing statuses are zero."""
    counts = {"scheduled": 0, "published": 0, "failed": 0}
    if not linkedin_is_connected(session):
        return counts
    account = _connected_account(session)
    rows = session.execute(
        select(ScheduledPost.status, func.count())
        .where(ScheduledPost.user_id == account.user_id)
        .group_by(ScheduledPost.status)
    ).all()
    for status, count in rows:
        name = status.value.lower() if isinstance(status, PostStatus) else str(status).lower()
        if name in counts:
            counts[name] = int(count)
    return counts


def _connected_account(session: Session) -> LinkedInAccount:
    account = session.scalar(select(LinkedInAccount).order_by(LinkedInAccount.updated_at.desc()))
    if account is None:
        raise NoLinkedInAccountError()
    return account


def _new_idempotency_key() -> uuid.UUID:
    return uuid.UUID(bytes=secrets.token_bytes(16))
