"""Authorization-code orchestration: state, token exchange, and account storage."""

from __future__ import annotations

import logging
import re
import secrets
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import LinkedInAccount, User
from app.schemas.linkedin_auth import LinkedInAccessToken
from app.services.linkedin.client import (
    LinkedInClient,
    LinkedInIdentityError,
    LinkedInOAuthError,
)

logger = logging.getLogger(__name__)

_STATE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_MEMBER_EMAIL_SAFE = re.compile(r"[^A-Za-z0-9._+-]")
STATE_COOKIE = "linkedin_oauth_state"


class MalformedCallbackError(Exception):
    code = "malformed_callback"


class InvalidOAuthStateError(Exception):
    code = "invalid_state"


class ExpiredOAuthStateError(Exception):
    code = "expired_state"


class OAuthStateStore:
    """Server-side, single-use OAuth state values."""

    def issue(self) -> str:
        raise NotImplementedError

    def consume(self, state: str) -> None:
        raise NotImplementedError


class InMemoryOAuthStateStore(OAuthStateStore):
    def __init__(
        self,
        ttl_seconds: int = 600,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._expires_at: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        state = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._purge(now)
            self._expires_at[state] = now + timedelta(seconds=self._ttl)
        return state

    def consume(self, state: str) -> None:
        if not _STATE_PATTERN.fullmatch(state):
            raise MalformedCallbackError()
        now = self._clock()
        with self._lock:
            expires_at = self._expires_at.pop(state, None)
            self._purge(now)
        if expires_at is None:
            raise InvalidOAuthStateError()
        if expires_at <= now:
            raise ExpiredOAuthStateError()

    def _purge(self, now: datetime) -> None:
        expired = [key for key, expires_at in self._expires_at.items() if expires_at <= now]
        for key in expired:
            del self._expires_at[key]


class LinkedInOAuthService:
    def __init__(self, settings: Settings, client: LinkedInClient, states: OAuthStateStore) -> None:
        self._settings = settings
        self._client = client
        self._states = states

    def start(self) -> tuple[str, str]:
        self._require_configuration()
        state = self._states.issue()
        url = self._client.authorization_url(state)
        logger.info("linkedin_oauth_started")
        return state, url

    def consume_state(self, state: str) -> None:
        self._states.consume(state)

    def complete(self, session: Session, code: str, state: str) -> None:
        self._require_configuration()
        if not _STATE_PATTERN.fullmatch(state) or not code.strip():
            raise MalformedCallbackError()
        self._states.consume(state)
        token = self._client.exchange_authorization_code(code)
        try:
            member = self._client.get_member_identity(token.access_token.get_secret_value())
        except LinkedInOAuthError:
            raise LinkedInIdentityError() from None
        self._save_account(session, member.sub, token)
        logger.info("linkedin_oauth_connected member_id=%s", member.sub)

    def _require_configuration(self) -> None:
        settings = self._settings
        if not settings.linkedin_client_id or not settings.linkedin_client_secret or not settings.linkedin_redirect_uri:
            raise LinkedInOAuthError("not_configured")

    def _save_account(self, session: Session, member_id: str, token: LinkedInAccessToken) -> None:
        account = session.scalar(
            select(LinkedInAccount).where(LinkedInAccount.linkedin_member_id == member_id)
        )
        if account is None:
            user = User(email=_member_email(member_id))
            session.add(user)
            session.flush()
            account = LinkedInAccount(user_id=user.id, linkedin_member_id=member_id)
            session.add(account)
        account.set_access_token(token.access_token.get_secret_value())
        refresh = token.refresh_token.get_secret_value() if token.refresh_token is not None else None
        account.set_refresh_token(refresh)
        account.token_expires_at = utcnow() + timedelta(seconds=token.expires_in)
        session.flush()


def is_well_formed_state(state: str | None) -> bool:
    return bool(state and _STATE_PATTERN.fullmatch(state))


def states_match(expected: str | None, provided: str | None) -> bool:
    if not expected or not provided:
        return False
    if not is_well_formed_state(expected) or not is_well_formed_state(provided):
        return False
    return secrets.compare_digest(expected, provided)


def _member_email(member_id: str) -> str:
    safe = _MEMBER_EMAIL_SAFE.sub("", member_id)[:64] or "member"
    return f"{safe}@member.linkedin.local"
