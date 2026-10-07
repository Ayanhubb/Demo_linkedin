from datetime import datetime, timezone
from typing import Any

from sqlalchemy import MetaData, event, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.db.types import AwareDateTime

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )


def _touch_updated_at(session: Session, flush_context: Any, instances: Any) -> None:
    del flush_context, instances
    now = utcnow()
    for obj in session.dirty:
        if isinstance(obj, TimestampMixin) and session.is_modified(obj, include_collections=False):
            obj.updated_at = now


event.listen(Session, "before_flush", _touch_updated_at)
