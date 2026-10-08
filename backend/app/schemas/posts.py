import base64
import binascii
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, PrivateAttr, field_validator, model_validator

from app.db.models.scheduled_post import PostStatus

_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif"})
_MAX_IMAGE_BYTES = 5_242_880


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
    image_base64: str | None = None
    image_content_type: str | None = None
    _image_bytes: bytes | None = PrivateAttr(default=None)

    @property
    def image_bytes(self) -> bytes | None:
        return self._image_bytes

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

    @model_validator(mode="after")
    def _decode_image(self) -> "SchedulePostRequest":
        raw = self.image_base64
        media_type = (self.image_content_type or "").strip().lower()
        if raw is None and not media_type:
            return self
        if raw is None or not media_type:
            raise ValueError("an image needs both image data and a content type")
        if media_type not in _IMAGE_TYPES:
            raise ValueError("image must be a JPEG, PNG, or GIF")
        encoded = raw.strip()
        if encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[-1]
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image data is not valid base64") from exc
        if not decoded or len(decoded) > _MAX_IMAGE_BYTES:
            raise ValueError("image must be between 1 byte and 5 MB")
        self._image_bytes = decoded
        self.image_content_type = media_type
        return self


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
    has_image: bool = False
    created_at: datetime

    @field_validator("status", mode="before")
    @classmethod
    def _lowercase_status(cls, value: object) -> str:
        if isinstance(value, PostStatus):
            return value.value.lower()
        return str(value).lower()
