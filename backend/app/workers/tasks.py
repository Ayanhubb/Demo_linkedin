"""Discover due posts, claim each row once, and publish it."""

from __future__ import annotations

import logging
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.logging import log_event
from app.db.base import utcnow
from app.db.database import session_scope
from app.db.models import LinkedInAccount, ScheduledPost
from app.db.models.scheduled_post import PostStatus
from app.services.linkedin.client import LinkedInClient
from app.services.linkedin.posts_service import (
    LinkedInPostsService,
    PermanentLinkedInPostError,
    TransientLinkedInPostError,
)
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
_CLAIM_BATCH_SIZE = 20
MAX_RETRIES = 4
_BACKOFF_CENTERS_SECONDS = (5, 15, 45, 120)
_JITTER_RATIO = 0.2


def retry_delay_seconds(attempt_count: int, *, rng: random.Random | None = None) -> float:
    """Jittered delay after a failed attempt. Centers are 5s, 15s, 45s, and 120s."""
    index = min(max(attempt_count, 1), MAX_RETRIES) - 1
    center = _BACKOFF_CENTERS_SECONDS[index]
    source = rng if rng is not None else random.SystemRandom()
    return center * source.uniform(1 - _JITTER_RATIO, 1 + _JITTER_RATIO)


def claim_due_posts(session: Session, *, now: datetime | None = None, limit: int = _CLAIM_BATCH_SIZE) -> list[uuid.UUID]:
    """Atomically move due SCHEDULED rows to PROCESSING. SKIP LOCKED skips rows another worker holds."""
    current = now or datetime.now(timezone.utc)
    session.flush()
    due_ids = (
        select(ScheduledPost.id)
        .where(
            ScheduledPost.status == PostStatus.SCHEDULED,
            ScheduledPost.scheduled_at <= current,
            or_(ScheduledPost.next_retry_at.is_(None), ScheduledPost.next_retry_at <= current),
        )
        .order_by(ScheduledPost.scheduled_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    claimed_rows = session.execute(
        update(ScheduledPost)
        .where(ScheduledPost.id.in_(due_ids))
        .values(
            status=PostStatus.PROCESSING,
            processing_started_at=current,
            attempt_count=ScheduledPost.attempt_count + 1,
            updated_at=current,
        )
        .returning(ScheduledPost.id, ScheduledPost.user_id, ScheduledPost.attempt_count)
    ).all()
    session.expire_all()
    claimed = [row.id for row in claimed_rows]
    for row in claimed_rows:
        log_event(
            logger,
            "post_claimed",
            post_id=row.id,
            user_id=row.user_id,
            status="PROCESSING",
            attempt_count=row.attempt_count,
        )
    return claimed


def publish_claimed_post(post_id: uuid.UUID, client: LinkedInClient | None = None) -> None:
    """Publish one claimed post. The row stays locked until the status update commits."""
    service = LinkedInPostsService(client=client)
    with session_scope() as session:
        post = session.get(ScheduledPost, post_id, with_for_update=True)
        if post is None:
            log_event(logger, "post_skipped", post_id=post_id, status="missing")
            return
        if post.status is PostStatus.PUBLISHED or post.linkedin_post_id:
            _finish_if_id_stored(post)
            log_event(
                logger,
                "post_skipped",
                post_id=post.id,
                user_id=post.user_id,
                status="already_persisted",
                attempt_count=post.attempt_count,
            )
            return
        if post.status is not PostStatus.PROCESSING:
            log_event(
                logger,
                "post_skipped",
                post_id=post.id,
                user_id=post.user_id,
                status=post.status.value,
                attempt_count=post.attempt_count,
            )
            return
        account = session.get(LinkedInAccount, post.linkedin_account_id)
        if account is None:
            _mark_failed(post, "missing_account")
            return
        started = time.perf_counter()
        try:
            if post.image_bytes:
                result = service.publish_image_post(
                    account,
                    post.content,
                    bytes(post.image_bytes),
                    post.image_content_type or "",
                )
            else:
                result = service.publish_text_post(account, post.content)
        except PermanentLinkedInPostError as exc:
            _mark_failed(post, exc.code, duration=time.perf_counter() - started)
            return
        except TransientLinkedInPostError as exc:
            _schedule_retry(post, exc.code)
            return
        except Exception as exc:
            log_event(
                logger,
                "linkedin_post_publish",
                level=logging.WARNING,
                post_id=post.id,
                user_id=post.user_id,
                status=type(exc).__name__,
                attempt_count=post.attempt_count,
                duration=time.perf_counter() - started,
            )
            _mark_failed(post, "unexpected_error")
            return
        if not result.linkedin_post_id:
            _mark_failed(post, "invalid_response")
            return
        _persist_published(session, post.id, result.linkedin_post_id, user_id=post.user_id, attempt_count=post.attempt_count, duration=time.perf_counter() - started)
        session.expire(post)


def process_due_posts_impl(client: LinkedInClient | None = None) -> dict[str, int]:
    with session_scope() as session:
        claimed_ids = claim_due_posts(session)
    for post_id in claimed_ids:
        publish_claimed_post(post_id, client=client)
    log_event(logger, "due_posts_processed", status="ok", attempt_count=len(claimed_ids))
    return {"claimed": len(claimed_ids)}


@celery_app.task(name="app.workers.tasks.process_due_posts", max_retries=0, autoretry_for=())
def process_due_posts() -> dict[str, int]:
    """Beat entry point. Transient retries are rows in PostgreSQL, not Celery redeliveries."""
    return process_due_posts_impl()


def _finish_if_id_stored(post: ScheduledPost) -> None:
    if not post.linkedin_post_id or post.status is PostStatus.PUBLISHED:
        return
    post.status = PostStatus.PUBLISHED
    if post.published_at is None:
        post.published_at = utcnow()
    post.last_error = None
    post.next_retry_at = None


def _persist_published(
    session: Session,
    post_id: uuid.UUID,
    linkedin_post_id: str,
    *,
    user_id: uuid.UUID | None = None,
    attempt_count: int | None = None,
    duration: float | None = None,
) -> None:
    """Write the LinkedIn id and PUBLISHED in one UPDATE. A second writer matches zero rows."""
    now = utcnow()
    result = session.execute(
        update(ScheduledPost)
        .where(
            ScheduledPost.id == post_id,
            ScheduledPost.linkedin_post_id.is_(None),
            ScheduledPost.status == PostStatus.PROCESSING,
        )
        .values(
            status=PostStatus.PUBLISHED,
            linkedin_post_id=linkedin_post_id,
            published_at=now,
            last_error=None,
            next_retry_at=None,
            updated_at=now,
        )
    )
    if result.rowcount == 1:
        log_event(
            logger,
            "linkedin_post_publish",
            post_id=post_id,
            user_id=user_id,
            status="success",
            attempt_count=attempt_count,
            duration=duration,
        )
        return
    log_event(logger, "post_skipped", post_id=post_id, user_id=user_id, status="publish_update_missed")


def _mark_failed(post: ScheduledPost, code: str, *, duration: float | None = None) -> None:
    post.status = PostStatus.FAILED
    post.last_error = code
    post.next_retry_at = None
    log_event(
        logger,
        "linkedin_post_publish",
        level=logging.WARNING,
        post_id=post.id,
        user_id=post.user_id,
        status=code,
        attempt_count=post.attempt_count,
        duration=duration,
    )


def _schedule_retry(post: ScheduledPost, code: str) -> None:
    post.last_error = code
    if post.attempt_count > MAX_RETRIES:
        post.status = PostStatus.FAILED
        post.next_retry_at = None
        log_event(
            logger,
            "linkedin_post_publish",
            level=logging.WARNING,
            post_id=post.id,
            user_id=post.user_id,
            status="failed",
            attempt_count=post.attempt_count,
        )
        return
    delay = retry_delay_seconds(post.attempt_count)
    post.status = PostStatus.SCHEDULED
    post.processing_started_at = None
    post.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
    log_event(
        logger,
        "post_retry_scheduled",
        level=logging.WARNING,
        post_id=post.id,
        user_id=post.user_id,
        status=code,
        attempt_count=post.attempt_count,
        duration=delay,
    )
