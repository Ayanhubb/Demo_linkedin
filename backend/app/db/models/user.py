import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.linkedin_account import LinkedInAccount
    from app.db.models.scheduled_post import ScheduledPost


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)

    linkedin_accounts: Mapped[list["LinkedInAccount"]] = relationship(back_populates="user")
    scheduled_posts: Mapped[list["ScheduledPost"]] = relationship(back_populates="user")

    def __repr__(self) -> str:
        return f"User(id={self.id!s}, email={self.email!r})"
