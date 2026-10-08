from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.db.models.linkedin_account import LinkedInAccount
from app.services.linkedin.client import POSTS_URL, LinkedInClient
from app.services.linkedin.posts_service import (
    PermanentLinkedInPostError,
    TransientLinkedInPostError,
    publish_image_post,
    publish_text_post,
)

ACCESS_TOKEN = "linkedin-access-token-do-not-store-plaintext"
MEMBER_ID = "abc123member"


class CapturedLinkedIn:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _account() -> LinkedInAccount:
    account = LinkedInAccount(user_id=uuid.uuid4(), linkedin_member_id=MEMBER_ID)
    account.set_access_token(ACCESS_TOKEN)
    return account


def _publish(captured: CapturedLinkedIn):
    client = LinkedInClient(get_settings(), transport=httpx.MockTransport(captured.handler))
    return publish_text_post(_account(), "Hello LinkedIn!", client=client)


def _assert_token_hidden(captured: CapturedLinkedIn, *values: object) -> None:
    rendered = " ".join(str(value) for value in values)
    assert ACCESS_TOKEN not in rendered
    assert captured.requests
    request = captured.requests[0]
    assert str(request.url) == POSTS_URL
    assert "ugcPosts" not in str(request.url)
    assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert request.headers["x-restli-protocol-version"] == "2.0.0"
    assert request.headers["linkedin-version"] == "202609"
    body = json.loads(request.content)
    assert body["author"] == f"urn:li:person:{MEMBER_ID}"
    assert body["commentary"] == "Hello LinkedIn!"
    assert body["visibility"] == "PUBLIC"
    assert body["lifecycleState"] == "PUBLISHED"


def test_successful_201_response() -> None:
    captured = CapturedLinkedIn(
        httpx.Response(201, headers={"x-restli-id": "urn:li:share:100"}, content=b"")
    )
    result = _publish(captured)
    assert result.success is True
    assert result.linkedin_post_id == "urn:li:share:100"
    assert result.http_status == 201
    assert result.metadata["x-restli-id"] == "urn:li:share:100"
    _assert_token_hidden(captured, result.model_dump())


def test_429_is_transient() -> None:
    captured = CapturedLinkedIn(httpx.Response(429, json={"code": "TOO_MANY_REQUESTS"}))
    with pytest.raises(TransientLinkedInPostError) as raised:
        _publish(captured)
    assert raised.value.http_status == 429
    assert raised.value.code == "rate_limited"
    assert raised.value.category == "transient"
    assert ACCESS_TOKEN not in str(raised.value)
    assert ACCESS_TOKEN not in str(raised.value.metadata)


def test_500_is_transient() -> None:
    captured = CapturedLinkedIn(httpx.Response(500, json={"message": "unavailable"}))
    with pytest.raises(TransientLinkedInPostError) as raised:
        _publish(captured)
    assert raised.value.http_status == 500
    assert raised.value.code == "server_error"
    assert raised.value.category == "transient"


def test_401_is_permanent_invalid_token() -> None:
    captured = CapturedLinkedIn(httpx.Response(401, json={"message": ACCESS_TOKEN}))
    with pytest.raises(PermanentLinkedInPostError) as raised:
        _publish(captured)
    assert raised.value.http_status == 401
    assert raised.value.code == "invalid_token"
    assert raised.value.category == "permanent"
    assert ACCESS_TOKEN not in str(raised.value)
    assert ACCESS_TOKEN not in str(raised.value.metadata)
    assert not isinstance(raised.value, TransientLinkedInPostError)


def test_403_is_permanent_permission_failure() -> None:
    captured = CapturedLinkedIn(httpx.Response(403, json={"code": "ACCESS_DENIED"}))
    with pytest.raises(PermanentLinkedInPostError) as raised:
        _publish(captured)
    assert raised.value.http_status == 403
    assert raised.value.code == "invalid_permission"
    assert raised.value.metadata["linkedin_code"] == "ACCESS_DENIED"


def test_timeout_is_transient() -> None:
    captured = CapturedLinkedIn(httpx.TimeoutException("timed out"))
    with pytest.raises(TransientLinkedInPostError) as raised:
        _publish(captured)
    assert raised.value.code == "timeout"
    assert raised.value.http_status is None
    assert raised.value.category == "transient"
    assert ACCESS_TOKEN not in str(raised.value)


def test_image_post_uploads_then_publishes() -> None:
    image = b"\x89PNG\r\n\x1a\nfake"
    upload_url = "https://www.linkedin.com/dms-uploads/image"
    image_urn = "urn:li:image:C4E10AQEXAMPLE"
    captured = CapturedLinkedIn(httpx.Response(500))
    responses = [
        httpx.Response(200, json={"value": {"uploadUrl": upload_url, "image": image_urn}}),
        httpx.Response(201, content=b""),
        httpx.Response(201, headers={"x-restli-id": "urn:li:share:200"}, content=b""),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        captured.requests.append(request)
        return responses.pop(0)

    client = LinkedInClient(get_settings(), transport=httpx.MockTransport(handler))
    result = publish_image_post(_account(), "Hello with image", image, "image/png", client=client)
    assert result.linkedin_post_id == "urn:li:share:200"
    assert len(captured.requests) == 3
    initialize = captured.requests[0]
    assert "action=initializeUpload" in str(initialize.url)
    assert json.loads(initialize.content)["initializeUploadRequest"]["owner"] == f"urn:li:person:{MEMBER_ID}"
    upload = captured.requests[1]
    assert str(upload.url) == upload_url
    assert upload.content == image
    assert upload.headers["content-type"] == "image/png"
    published = json.loads(captured.requests[2].content)
    assert published["commentary"] == "Hello with image"
    assert published["content"]["media"]["id"] == image_urn
    assert ACCESS_TOKEN not in str(result.model_dump())


def test_malformed_success_response_is_permanent() -> None:
    captured = CapturedLinkedIn(httpx.Response(201, content=b"not-json"))
    with pytest.raises(PermanentLinkedInPostError) as raised:
        _publish(captured)
    assert raised.value.http_status == 201
    assert raised.value.code == "invalid_response"
    assert raised.value.category == "permanent"
    assert ACCESS_TOKEN not in str(raised.value)
