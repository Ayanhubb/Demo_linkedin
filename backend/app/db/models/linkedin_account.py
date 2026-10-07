import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.security import TokenStorageError, decrypt_secret, encrypt_secret
from app.db.base import Base, TimestampMixin
from app.db.types import AwareDateTime

if TYPE_CHECKING:
    from app.db.models.scheduled_post import ScheduledPost
    from app.db.models.user import User


class LinkedInAccount(TimestampMixin, Base):
    __tablename__ = "linkedin_accounts"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_linkedin_accounts_id_user"),
        UniqueConstraint("user_id", "linkedin_member_id", name="uq_linkedin_accounts_user_member"),
        Index("ix_linkedin_accounts_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_linkedin_accounts_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    linkedin_member_id: Mapped[str] = mapped_column(String(255), nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)

    user: Mapped["User"] = relationship(back_populates="linkedin_accounts")
    scheduled_posts: Mapped[list["ScheduledPost"]] = relationship(
        back_populates="linkedin_account",
        foreign_keys="ScheduledPost.linkedin_account_id",
    )

    def set_access_token(self, plaintext: str) -> None:
        self.access_token_encrypted = encrypt_secret(plaintext)

    def set_refresh_token(self, plaintext: str | None) -> None:
        self.refresh_token_encrypted = None if plaintext is None else encrypt_secret(plaintext)

    def read_access_token(self) -> str:
        return decrypt_secret(self.access_token_encrypted)

    def read_refresh_token(self) -> str | None:
        if self.refresh_token_encrypted is None:
            return None
        return decrypt_secret(self.refresh_token_encrypted)

    @validates("access_token_encrypted", "refresh_token_encrypted")
    def _reject_plaintext_token(self, key: str, value: str | None) -> str | None:
        del key
        if value is None:
            return None
        try:
            decrypt_secret(value)
        except TokenStorageError:
            raise TokenStorageError("OAuth token must be encrypted before it is stored") from None
        return value

    def __repr__(self) -> str:
        return (
            f"LinkedInAccount(id={self.id!s}, user_id={self.user_id!s}, "
            f"linkedin_member_id={self.linkedin_member_id!r})"
        )
