import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_validator

from app.db.models.scheduled_post import PostStatus


class SchedulePostRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "content": "Hello from the scheduler.",
                    "scheduled_at": "2026-10-08T10:00:00+05:30",
                }
            ]
        }
    )

    content: str
    scheduled_at: datetime

    @field_validator("content")
    @classmethod
    def _content_bounds(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content cannot be empty")
        if len(stripped) > 3000:
            raise ValueError("content must be at most 3000 characters")
        return stripped

    @field_validator("scheduled_at")
    @classmethod
    def _future_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must include a timezone")
        if value.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError("scheduled_at must be in the future")
        return value


class ScheduledPostResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "content": "Hello from the scheduler.",
                    "scheduled_at": "2026-10-08T04:30:00Z",
                    "status": "scheduled",
                    "attempt_count": 0,
                    "last_error": None,
                    "created_at": "2026-10-07T18:00:00Z",
                }
            ]
        },
    )

    id: uuid.UUID
    content: str
    scheduled_at: datetime
    status: str
    attempt_count: int
    last_error: str | None = None
    created_at: datetime

    @field_validator("status", mode="before")
    @classmethod
    def _lowercase_status(cls, value: object) -> str:
        if isinstance(value, PostStatus):
            return value.value.lower()
        return str(value).lower()
