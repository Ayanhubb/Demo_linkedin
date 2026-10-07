from datetime import datetime

from sqlalchemy import DateTime, TypeDecorator
from sqlalchemy.engine import Dialect


class AwareDateTime(TypeDecorator[datetime]):
    """TIMESTAMP WITH TIME ZONE that rejects naive datetimes."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamps must be timezone-aware")
        return value
