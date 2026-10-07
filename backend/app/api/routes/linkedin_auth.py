"""LinkedIn OAuth 2.0 authorization-code endpoints."""

import logging
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.database import get_db
from app.schemas.linkedin_auth import OAuthErrorResponse
from app.services.linkedin.client import LinkedInClient, LinkedInIdentityError, LinkedInOAuthError
from app.services.linkedin.oauth_service import (
    STATE_COOKIE,
    ExpiredOAuthStateError,
    InMemoryOAuthStateStore,
    InvalidOAuthStateError,
    LinkedInOAuthService,
    MalformedCallbackError,
    OAuthStateStore,
    is_well_formed_state,
    states_match,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["linkedin-auth"])

_PROVIDER_ERRORS = frozenset(
    {"access_denied", "invalid_request", "invalid_scope", "server_error", "temporarily_unavailable"}
)
_ERROR_MESSAGES = {
    "not_configured": "LinkedIn OAuth is not configured",
    "malformed_callback": "The LinkedIn callback is missing required fields",
    "invalid_state": "The OAuth state is invalid",
    "expired_state": "The OAuth state has expired",
    "token_exchange_failed": "LinkedIn did not issue an access token",
    "identity_failed": "LinkedIn did not return the member id",
    "access_denied": "LinkedIn authorization was denied",
    "oauth_error": "LinkedIn authorization failed",
}


def get_state_store(request: Request) -> OAuthStateStore:
    store = getattr(request.app.state, "oauth_state_store", None)
    if store is None:
        store = InMemoryOAuthStateStore(ttl_seconds=get_settings().oauth_state_ttl_seconds)
        request.app.state.oauth_state_store = store
    return store


def get_linkedin_client(settings: Settings = Depends(get_settings)) -> LinkedInClient:
    return LinkedInClient(settings)


def get_oauth_service(
    settings: Settings = Depends(get_settings),
    client: LinkedInClient = Depends(get_linkedin_client),
    states: OAuthStateStore = Depends(get_state_store),
) -> LinkedInOAuthService:
    return LinkedInOAuthService(settings, client, states)


@router.get(
    "/api/auth/linkedin",
    response_model=None,
    summary="Start LinkedIn OAuth",
    description="Redirects the browser to LinkedIn. Returns JSON only when OAuth is not configured.",
    responses={
        302: {"description": "Redirect to LinkedIn's authorization page."},
        503: {
            "model": OAuthErrorResponse,
            "description": "LINKEDIN_CLIENT_ID or LINKEDIN_CLIENT_SECRET is empty.",
        },
    },
)
def start_linkedin_oauth(
    service: LinkedInOAuthService = Depends(get_oauth_service),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse | JSONResponse:
    try:
        state, url = service.start()
    except LinkedInOAuthError as exc:
        logger.warning("linkedin_oauth_failed error=%s", exc.code)
        return _json_error(exc.code, 503)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        key=STATE_COOKIE,
        value=state,
        max_age=settings.oauth_state_ttl_seconds,
        httponly=True,
        secure=urlsplit(settings.linkedin_redirect_uri).scheme == "https",
        samesite="lax",
        path="/api/auth/linkedin/callback",
    )
    return response


@router.get(
    "/api/auth/linkedin/callback",
    response_model=None,
    summary="Complete LinkedIn OAuth",
    description="Validates state, exchanges the code, stores encrypted tokens, and redirects to the frontend.",
    responses={302: {"description": "Redirect to the frontend with linkedin=connected or linkedin_error."}},
)
def linkedin_oauth_callback(
    request: Request,
    service: LinkedInOAuthService = Depends(get_oauth_service),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db),
) -> RedirectResponse:
    error = request.query_params.get("error")
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    cookie_state = request.cookies.get(STATE_COOKIE)
    try:
        if error:
            if not is_well_formed_state(state):
                raise MalformedCallbackError()
            if not states_match(cookie_state, state):
                raise InvalidOAuthStateError()
            service.consume_state(state or "")
            safe_error = error if error in _PROVIDER_ERRORS else "oauth_error"
            raise LinkedInOAuthError(safe_error)
        if not code or not state or not code.strip() or not is_well_formed_state(state):
            raise MalformedCallbackError()
        if not states_match(cookie_state, state):
            raise InvalidOAuthStateError()
        service.complete(session, code, state)
    except (MalformedCallbackError, InvalidOAuthStateError, ExpiredOAuthStateError) as exc:
        logger.warning("linkedin_oauth_failed error=%s", exc.code)
        return _frontend_redirect(settings.frontend_origin, linkedin_error=exc.code)
    except LinkedInIdentityError as exc:
        logger.warning("linkedin_oauth_failed error=%s", exc.code)
        return _frontend_redirect(settings.frontend_origin, linkedin_error=exc.code)
    except LinkedInOAuthError as exc:
        public_code = exc.code if exc.code in _ERROR_MESSAGES else "token_exchange_failed"
        if exc.code in _PROVIDER_ERRORS or exc.code == "oauth_error":
            public_code = exc.code
        logger.warning("linkedin_oauth_failed error=%s", public_code)
        return _frontend_redirect(settings.frontend_origin, linkedin_error=public_code)
    return _frontend_redirect(settings.frontend_origin, linkedin="connected")


def _frontend_redirect(origin: str, **params: str) -> RedirectResponse:
    url = str(httpx_url(origin, params))
    response = RedirectResponse(url, status_code=302)
    response.delete_cookie(STATE_COOKIE, path="/api/auth/linkedin/callback")
    return response


def httpx_url(origin: str, params: dict[str, str]) -> str:
    return str(httpx.URL(origin).copy_with(params=params))


def _json_error(code: str, status_code: int) -> JSONResponse:
    body = OAuthErrorResponse(error=code, message=_ERROR_MESSAGES.get(code, _ERROR_MESSAGES["oauth_error"]))
    return JSONResponse(status_code=status_code, content=body.model_dump())
