"""Schedule, list, fetch, and cancel LinkedIn posts."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models.scheduled_post import PostStatus
from app.schemas.errors import ErrorResponse
from app.schemas.posts import ScheduledPostResponse, SchedulePostRequest
from app.services.scheduler.schedule_service import (
    NoLinkedInAccountError,
    ScheduledPostNotDeletableError,
    ScheduledPostNotFoundError,
    create_scheduled_post,
    delete_scheduled_post,
    get_scheduled_post,
    list_scheduled_posts,
)

router = APIRouter(tags=["posts"])


_ERROR = {"model": ErrorResponse}


@router.post(
    "/api/posts/schedule",
    response_model=ScheduledPostResponse,
    status_code=201,
    summary="Schedule a text post",
    responses={409: _ERROR, 422: _ERROR},
)
def schedule_post(
    body: SchedulePostRequest,
    session: Session = Depends(get_db),
) -> ScheduledPostResponse:
    try:
        post = create_scheduled_post(session, body.content, body.scheduled_at)
    except NoLinkedInAccountError:
        raise HTTPException(status_code=409, detail="A connected LinkedIn account is required") from None
    return ScheduledPostResponse.model_validate(post)


@router.get(
    "/api/posts",
    response_model=list[ScheduledPostResponse],
    summary="List scheduled posts",
    responses={409: _ERROR, 422: _ERROR},
)
def list_posts(
    status: str | None = Query(default=None),
    session: Session = Depends(get_db),
) -> list[ScheduledPostResponse]:
    try:
        posts = list_scheduled_posts(session, _parse_status(status))
    except NoLinkedInAccountError:
        raise HTTPException(status_code=409, detail="A connected LinkedIn account is required") from None
    return [ScheduledPostResponse.model_validate(post) for post in posts]


@router.get(
    "/api/posts/{post_id}",
    response_model=ScheduledPostResponse,
    summary="Fetch one scheduled post",
    responses={404: _ERROR, 409: _ERROR},
)
def get_post(
    post_id: uuid.UUID,
    session: Session = Depends(get_db),
) -> ScheduledPostResponse:
    try:
        post = get_scheduled_post(session, post_id)
    except NoLinkedInAccountError:
        raise HTTPException(status_code=409, detail="A connected LinkedIn account is required") from None
    except ScheduledPostNotFoundError:
        raise HTTPException(status_code=404, detail="Post not found") from None
    return ScheduledPostResponse.model_validate(post)


@router.delete(
    "/api/posts/{post_id}",
    status_code=204,
    summary="Delete a post that LinkedIn has not accepted",
    responses={404: _ERROR, 409: _ERROR},
)
def delete_post(
    post_id: uuid.UUID,
    session: Session = Depends(get_db),
) -> Response:
    try:
        delete_scheduled_post(session, post_id)
    except NoLinkedInAccountError:
        raise HTTPException(status_code=409, detail="A connected LinkedIn account is required") from None
    except ScheduledPostNotFoundError:
        raise HTTPException(status_code=404, detail="Post not found") from None
    except ScheduledPostNotDeletableError:
        raise HTTPException(status_code=409, detail="Published posts cannot be deleted") from None
    return Response(status_code=204)


def _parse_status(value: str | None) -> PostStatus | None:
    if value is None:
        return None
    try:
        return PostStatus(value.strip().upper())
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="status must be scheduled, processing, published, or failed",
        ) from None
