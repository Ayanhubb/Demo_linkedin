"""Process health, including PostgreSQL and Redis."""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.db.database import get_engine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=None,
    summary="Process health",
    responses={
        200: {
            "description": "PostgreSQL and Redis both answered.",
            "content": {"application/json": {"example": {"status": "ok"}}},
        },
        503: {
            "description": "A dependency did not answer.",
            "content": {
                "application/json": {"example": {"status": "unavailable", "dependency": "redis"}}
            },
        },
    },
)
def health() -> JSONResponse:
    if not postgres_ready():
        return JSONResponse(status_code=503, content={"status": "unavailable", "dependency": "postgres"})
    if not redis_ready():
        return JSONResponse(status_code=503, content={"status": "unavailable", "dependency": "redis"})
    return JSONResponse(content={"status": "ok"})


def postgres_ready() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("health_check_failed dependency=postgres error_type=%s", type(exc).__name__)
        return False
    return True


def redis_ready() -> bool:
    try:
        import redis

        client = redis.Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        return bool(client.ping())
    except Exception as exc:
        logger.warning("health_check_failed dependency=redis error_type=%s", type(exc).__name__)
        return False
