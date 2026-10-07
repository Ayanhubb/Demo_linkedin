"""API errors. Clients receive a short code and message. Traces stay in the logs."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import log_event
from app.services.linkedin.client import LinkedInOAuthError
from app.services.linkedin.posts_service import LinkedInPostError

logger = logging.getLogger(__name__)

_STATUS_ERRORS = {
    400: "bad_request",
    401: "authentication_error",
    403: "authentication_error",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    500: "internal_error",
    502: "linkedin_api_error",
    503: "service_unavailable",
}


def error_body(error: str, message: str) -> dict[str, str]:
    return {"error": error, "message": message}


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(LinkedInOAuthError, _linkedin_oauth_error)
    app.add_exception_handler(LinkedInPostError, _linkedin_post_error)
    app.add_exception_handler(SQLAlchemyError, _database_error)
    app.add_exception_handler(Exception, _unexpected_error)


async def _validation_error(_request: Request, exc: Exception) -> JSONResponse:
    details: list[str] = []
    if isinstance(exc, RequestValidationError):
        for item in exc.errors():
            location = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
            message = str(item.get("msg", "Invalid value"))
            details.append(f"{location}: {message}" if location else message)
    message = details[0] if details else "Request validation failed"
    return JSONResponse(status_code=422, content=error_body("validation_error", message))


async def _http_error(_request: Request, exc: Exception) -> JSONResponse:
    status_code = exc.status_code if isinstance(exc, HTTPException) else 500
    detail = exc.detail if isinstance(exc, HTTPException) else "Request failed"
    message = detail if isinstance(detail, str) else "Request failed"
    error = _STATUS_ERRORS.get(status_code, "request_error")
    return JSONResponse(status_code=status_code, content=error_body(error, message))


async def _linkedin_oauth_error(_request: Request, exc: Exception) -> JSONResponse:
    code = exc.code if isinstance(exc, LinkedInOAuthError) else "oauth_error"
    log_event(logger, "linkedin_oauth_error", level=logging.WARNING, status=code)
    status_code = 503 if code == "not_configured" else 401
    return JSONResponse(
        status_code=status_code,
        content=error_body("authentication_error", "LinkedIn authorization failed"),
    )


async def _linkedin_post_error(_request: Request, exc: Exception) -> JSONResponse:
    category = getattr(exc, "category", "permanent")
    log_event(logger, "linkedin_api_error", level=logging.WARNING, status=category)
    if category == "transient":
        return JSONResponse(
            status_code=503,
            content=error_body("linkedin_api_error", "LinkedIn temporarily unavailable"),
        )
    return JSONResponse(
        status_code=502,
        content=error_body("linkedin_api_error", "LinkedIn rejected the post"),
    )


async def _database_error(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("database_error error_type=%s", type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content=error_body("database_error", "The database request could not be completed"),
    )


async def _unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unexpected_error error_type=%s", type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content=error_body("internal_error", "Unexpected server error"),
    )
