"""Publish one public text or image post for a LinkedIn member."""

from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import redact_text
from app.db.models.linkedin_account import LinkedInAccount
from app.services.linkedin.client import LinkedInCallResult, LinkedInClient

logger = logging.getLogger(__name__)

POSTS_URL = "https://api.linkedin.com/rest/posts"
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
ErrorCategory = Literal["transient", "permanent"]


class TextPostPublishResult(BaseModel):
    success: bool
    linkedin_post_id: str | None = None
    http_status: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class LinkedInPostError(Exception):
    """A classified Posts API failure. The message is a short code, never a token."""

    def __init__(
        self,
        code: str,
        *,
        category: ErrorCategory,
        http_status: int | None,
        metadata: dict[str, str],
    ) -> None:
        self.code = code
        self.category = category
        self.http_status = http_status
        self.metadata = metadata
        super().__init__(code)


class TransientLinkedInPostError(LinkedInPostError):
    def __init__(self, code: str, *, http_status: int | None, metadata: dict[str, str]) -> None:
        super().__init__(code, category="transient", http_status=http_status, metadata=metadata)


class PermanentLinkedInPostError(LinkedInPostError):
    def __init__(self, code: str, *, http_status: int | None, metadata: dict[str, str]) -> None:
        super().__init__(code, category="permanent", http_status=http_status, metadata=metadata)


def publish_text_post(
    linkedin_account: LinkedInAccount,
    content: str,
    *,
    client: LinkedInClient | None = None,
) -> TextPostPublishResult:
    """Publish one text post. This function does not retry."""
    commentary = content.strip()
    if not commentary or len(commentary) > 3000:
        raise PermanentLinkedInPostError(
            "invalid_payload",
            http_status=None,
            metadata={"reason": "content must be 1 to 3000 characters"},
        )
    author = _member_urn(linkedin_account.linkedin_member_id)
    access_token = linkedin_account.read_access_token()
    linkedin = client or LinkedInClient(get_settings())
    result = linkedin.publish_text_post(
        access_token=access_token,
        payload=_text_post_payload(author, commentary),
    )
    if result.transport_error == "timeout":
        _log_failure("timeout", None, "transient")
        raise TransientLinkedInPostError("timeout", http_status=None, metadata={})
    if result.transport_error == "connection":
        _log_failure("connection_failed", None, "transient")
        raise TransientLinkedInPostError("connection_failed", http_status=None, metadata={})
    return _interpret(result, access_token)


def publish_image_post(
    linkedin_account: LinkedInAccount,
    content: str,
    image: bytes,
    content_type: str,
    *,
    client: LinkedInClient | None = None,
) -> TextPostPublishResult:
    """Upload one image, then publish a post that uses it. This function does not retry."""
    commentary = content.strip()
    if not commentary or len(commentary) > 3000:
        raise PermanentLinkedInPostError(
            "invalid_payload",
            http_status=None,
            metadata={"reason": "content must be 1 to 3000 characters"},
        )
    if not image or len(image) > 5_242_880:
        raise PermanentLinkedInPostError(
            "invalid_payload",
            http_status=None,
            metadata={"reason": "image must be between 1 byte and 5 MB"},
        )
    author = _member_urn(linkedin_account.linkedin_member_id)
    access_token = linkedin_account.read_access_token()
    linkedin = client or LinkedInClient(get_settings())
    initialized = linkedin.initialize_image_upload(access_token=access_token, owner=author)
    _raise_unless(initialized, access_token, {200})
    upload_url, image_urn = _image_target(initialized, access_token)
    uploaded = linkedin.upload_image(
        access_token=access_token,
        upload_url=upload_url,
        image=image,
        content_type=content_type,
    )
    _raise_unless(uploaded, access_token, {200, 201})
    payload = _text_post_payload(author, commentary)
    payload["content"] = {"media": {"id": image_urn}}
    result = linkedin.publish_text_post(access_token=access_token, payload=payload)
    return _interpret(result, access_token)


def _member_urn(member_id: str) -> str:
    if member_id.startswith("urn:li:person:"):
        return member_id
    if not member_id.strip():
        raise PermanentLinkedInPostError(
            "invalid_payload",
            http_status=None,
            metadata={"reason": "linkedin member id is missing"},
        )
    return f"urn:li:person:{member_id}"


def _text_post_payload(author: str, commentary: str) -> dict[str, object]:
    return {
        "author": author,
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def _raise_unless(result: LinkedInCallResult, access_token: str, success: set[int]) -> None:
    if result.transport_error == "timeout":
        _log_failure("timeout", None, "transient")
        raise TransientLinkedInPostError("timeout", http_status=None, metadata={})
    if result.transport_error == "connection":
        _log_failure("connection_failed", None, "transient")
        raise TransientLinkedInPostError("connection_failed", http_status=None, metadata={})
    if result.status_code in success:
        return
    metadata = _safe_metadata(result, access_token)
    if result.status_code in _TRANSIENT_STATUSES:
        code = "rate_limited" if result.status_code == 429 else "server_error"
        _log_failure(code, result.status_code, "transient")
        raise TransientLinkedInPostError(code, http_status=result.status_code, metadata=metadata)
    code = _permanent_code(result.status_code)
    _log_failure(code, result.status_code, "permanent")
    raise PermanentLinkedInPostError(code, http_status=result.status_code, metadata=metadata)


def _image_target(result: LinkedInCallResult, access_token: str) -> tuple[str, str]:
    metadata = _safe_metadata(result, access_token)
    try:
        payload = json.loads(result.body_text)
    except json.JSONDecodeError:
        payload = None
    value = payload.get("value") if isinstance(payload, dict) else None
    upload_url = value.get("uploadUrl") if isinstance(value, dict) else None
    image_urn = value.get("image") if isinstance(value, dict) else None
    if (
        not isinstance(upload_url, str)
        or not upload_url.startswith("https://")
        or not isinstance(image_urn, str)
        or not image_urn.startswith("urn:li:image:")
    ):
        _log_failure("invalid_response", result.status_code, "permanent")
        raise PermanentLinkedInPostError(
            "invalid_response",
            http_status=result.status_code,
            metadata=metadata,
        )
    return upload_url, image_urn


def _interpret(result: LinkedInCallResult, access_token: str) -> TextPostPublishResult:
    status = result.status_code
    metadata = _safe_metadata(result, access_token)
    if result.transport_error == "timeout":
        _log_failure("timeout", None, "transient")
        raise TransientLinkedInPostError("timeout", http_status=None, metadata={})
    if result.transport_error == "connection":
        _log_failure("connection_failed", None, "transient")
        raise TransientLinkedInPostError("connection_failed", http_status=None, metadata={})
    if status == 201:
        post_id = _post_id(result)
        if not post_id:
            _log_failure("invalid_response", status, "permanent")
            raise PermanentLinkedInPostError(
                "invalid_response",
                http_status=status,
                metadata=metadata,
            )
        logger.info("linkedin_post_published status=%s", status)
        return TextPostPublishResult(
            success=True,
            linkedin_post_id=post_id,
            http_status=status,
            metadata=metadata,
        )
    if status in _TRANSIENT_STATUSES:
        code = "rate_limited" if status == 429 else "server_error"
        _log_failure(code, status, "transient")
        raise TransientLinkedInPostError(code, http_status=status, metadata=metadata)
    code = _permanent_code(status)
    _log_failure(code, status, "permanent")
    raise PermanentLinkedInPostError(code, http_status=status, metadata=metadata)


def _permanent_code(status: int | None) -> str:
    if status == 401:
        return "invalid_token"
    if status == 403:
        return "invalid_permission"
    if status in {400, 422}:
        return "invalid_payload"
    return "client_error"


def _post_id(result: LinkedInCallResult) -> str | None:
    for key, value in result.headers.items():
        if key.lower() == "x-restli-id" and value.strip():
            return value.strip()
    if not result.body_text.strip():
        return None
    try:
        payload = json.loads(result.body_text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        post_id = payload.get("id")
        if isinstance(post_id, str) and post_id.strip():
            return post_id.strip()
    return None


def _safe_metadata(result: LinkedInCallResult, access_token: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for key, value in result.headers.items():
        lowered = key.lower()
        if lowered in {"x-restli-id", "x-li-uuid", "x-restli-protocol-version"}:
            metadata[lowered] = _redact(value, access_token)
    if not result.body_text.strip():
        return metadata
    try:
        payload = json.loads(result.body_text)
    except json.JSONDecodeError:
        return metadata
    if not isinstance(payload, dict):
        return metadata
    code = payload.get("code") or payload.get("serviceErrorCode")
    if isinstance(code, str | int) and str(code) and len(str(code)) <= 64:
        metadata["linkedin_code"] = _redact(str(code), access_token)
    return metadata


def _redact(value: str, access_token: str) -> str:
    redacted = value.replace(access_token, "[REDACTED]") if access_token else value
    return redact_text(redacted)


def _log_failure(code: str, status: int | None, category: str) -> None:
    logger.warning(
        "linkedin_post_failed category=%s status=%s error_code=%s",
        category,
        status,
        code,
    )


class LinkedInPostsService:
    """Posts API facade used by the background worker."""

    def __init__(self, client: LinkedInClient | None = None) -> None:
        self._client = client

    def publish_text_post(self, linkedin_account: LinkedInAccount, content: str) -> TextPostPublishResult:
        return publish_text_post(linkedin_account, content, client=self._client)

    def publish_image_post(
        self,
        linkedin_account: LinkedInAccount,
        content: str,
        image: bytes,
        content_type: str,
    ) -> TextPostPublishResult:
        return publish_image_post(
            linkedin_account,
            content,
            image,
            content_type,
            client=self._client,
        )
