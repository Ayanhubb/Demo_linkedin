import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.types import AwareDateTime

if TYPE_CHECKING:
    from app.db.models.linkedin_account import LinkedInAccount
    from app.db.models.user import User


class PostStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    PROCESSING = "PROCESSING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


def _status_values(enum_cls: type[PostStatus]) -> list[str]:
    return [member.value for member in enum_cls]


class ScheduledPost(TimestampMixin, Base):
    __tablename__ = "scheduled_posts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["linkedin_account_id", "user_id"],
            ["linkedin_accounts.id", "linkedin_accounts.user_id"],
            name="fk_scheduled_posts_account_same_user",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "char_length(content) BETWEEN 1 AND 3000",
            name="ck_scheduled_posts_content_length",
        ),
        CheckConstraint(
            "(image_bytes IS NULL AND image_content_type IS NULL) OR "
            "(image_bytes IS NOT NULL AND image_content_type IS NOT NULL "
            "AND octet_length(image_bytes) BETWEEN 1 AND 5242880)",
            name="ck_scheduled_posts_image_pair",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_scheduled_posts_attempt_count_nonnegative",
        ),
        CheckConstraint(
            "max_attempts >= 1",
            name="ck_scheduled_posts_max_attempts_positive",
        ),
        Index("ix_scheduled_posts_status_scheduled_at", "status", "scheduled_at"),
        Index("ix_scheduled_posts_linkedin_account_id", "linkedin_account_id"),
        Index("ix_scheduled_posts_idempotency_key", "idempotency_key", unique=True),
        Index("ix_scheduled_posts_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_scheduled_posts_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    linkedin_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "linkedin_accounts.id",
            name="fk_scheduled_posts_linkedin_account_id_linkedin_accounts",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    image_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    image_content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    status: Mapped[PostStatus] = mapped_column(
        Enum(
            PostStatus,
            name="post_status",
            native_enum=True,
            values_callable=_status_values,
        ),
        nullable=False,
        default=PostStatus.SCHEDULED,
        server_default=text("'SCHEDULED'"),
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    linkedin_post_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)

    @property
    def has_image(self) -> bool:
        return self.image_bytes is not None

    user: Mapped["User"] = relationship(back_populates="scheduled_posts")
    linkedin_account: Mapped["LinkedInAccount"] = relationship(
        back_populates="scheduled_posts",
        foreign_keys=[linkedin_account_id],
    )

    def __repr__(self) -> str:
        return (
            f"ScheduledPost(id={self.id!s}, status={self.status!s}, "
            f"idempotency_key={self.idempotency_key!s})"
        )
