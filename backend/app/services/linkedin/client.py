"""LinkedIn API calls for the authorization-code flow."""

from pydantic import ValidationError

import httpx

from app.core.config import Settings
from app.schemas.linkedin_auth import LinkedInAccessToken, LinkedInMemberIdentity

AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
POSTS_URL = "https://api.linkedin.com/rest/posts"
IMAGES_URL = "https://api.linkedin.com/rest/images"
REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
UPLOAD_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


class LinkedInOAuthError(Exception):
    """LinkedIn rejected or did not complete an OAuth request."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("LinkedIn OAuth request failed")


class LinkedInCallResult:
    """HTTP result that never includes the Authorization header."""

    def __init__(
        self,
        *,
        status_code: int | None,
        headers: dict[str, str],
        body_text: str,
        transport_error: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers
        self.body_text = body_text
        self.transport_error = transport_error


class LinkedInIdentityError(Exception):
    """The member identity endpoint did not return a usable member id."""

    def __init__(self) -> None:
        self.code = "identity_failed"
        super().__init__("LinkedIn member identity request failed")


def requested_scopes(configured: str) -> str:
    """Return the scopes to request.

    `w_member_social` is the posting permission. The current member-identity
    endpoint is OpenID Connect userinfo, which requires `openid`. Deprecated
    scopes such as `r_liteprofile` are not added.
    """
    scopes: list[str] = []
    for scope in configured.split():
        if scope not in scopes:
            scopes.append(scope)
    if "openid" not in scopes:
        scopes.insert(0, "openid")
    return " ".join(scopes)


class LinkedInClient:
    """HTTP client for LinkedIn's current 3-legged OAuth endpoints."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    def authorization_url(self, state: str) -> str:
        if not self._settings.linkedin_client_id or not self._settings.linkedin_redirect_uri:
            raise LinkedInOAuthError("not_configured")
        url = httpx.URL(
            AUTHORIZE_URL,
            params={
                "response_type": "code",
                "client_id": self._settings.linkedin_client_id,
                "redirect_uri": self._settings.linkedin_redirect_uri,
                "state": state,
                "scope": requested_scopes(self._settings.linkedin_scopes),
            },
        )
        rendered = str(url)
        if "client_secret" in rendered or self._settings.linkedin_client_secret and (
            self._settings.linkedin_client_secret in rendered
        ):
            raise LinkedInOAuthError("invalid_authorize_url")
        return rendered

    def exchange_authorization_code(self, code: str) -> LinkedInAccessToken:
        if not self._settings.linkedin_client_secret:
            raise LinkedInOAuthError("not_configured")
        payload = self._request(
            "POST",
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._settings.linkedin_redirect_uri,
                "client_id": self._settings.linkedin_client_id,
                "client_secret": self._settings.linkedin_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            return LinkedInAccessToken.model_validate(payload)
        except ValidationError:
            raise LinkedInOAuthError("invalid_response") from None

    def get_member_identity(self, access_token: str) -> LinkedInMemberIdentity:
        payload = self._request(
            "GET",
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        try:
            return LinkedInMemberIdentity.model_validate(payload)
        except ValidationError:
            raise LinkedInIdentityError() from None

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, transport=self._transport) as client:
                response = client.request(method, url, data=data, headers=headers)
        except httpx.TimeoutException:
            raise LinkedInOAuthError("timeout") from None
        except httpx.HTTPError:
            raise LinkedInOAuthError("network") from None
        if response.status_code >= 400:
            raise LinkedInOAuthError(_safe_error_code(response))
        try:
            payload = response.json()
        except ValueError:
            raise LinkedInOAuthError("invalid_response") from None
        if not isinstance(payload, dict):
            raise LinkedInOAuthError("invalid_response")
        return payload

    def publish_text_post(self, *, access_token: str, payload: dict[str, object]) -> LinkedInCallResult:
        """POST a text post. Transport failures are returned instead of raised."""
        return self._api_call("POST", POSTS_URL, access_token=access_token, json_body=payload)

    def initialize_image_upload(self, *, access_token: str, owner: str) -> LinkedInCallResult:
        """Register an image owned by the member and receive an upload URL."""
        return self._api_call(
            "POST",
            f"{IMAGES_URL}?action=initializeUpload",
            access_token=access_token,
            json_body={"initializeUploadRequest": {"owner": owner}},
        )

    def upload_image(
        self,
        *,
        access_token: str,
        upload_url: str,
        image: bytes,
        content_type: str,
    ) -> LinkedInCallResult:
        """PUT image bytes to the upload URL LinkedIn returned."""
        return self._api_call(
            "PUT",
            upload_url,
            access_token=access_token,
            content=image,
            content_type=content_type,
            timeout=UPLOAD_TIMEOUT,
        )

    def _api_call(
        self,
        method: str,
        url: str,
        *,
        access_token: str,
        json_body: dict[str, object] | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
        timeout: httpx.Timeout = REQUEST_TIMEOUT,
    ) -> LinkedInCallResult:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Linkedin-Version": self._settings.linkedin_version,
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        elif content_type:
            headers["Content-Type"] = content_type
        try:
            with httpx.Client(timeout=timeout, transport=self._transport, follow_redirects=False) as client:
                response = client.request(method, url, json=json_body, content=content, headers=headers)
        except httpx.TimeoutException:
            return LinkedInCallResult(status_code=None, headers={}, body_text="", transport_error="timeout")
        except httpx.HTTPError:
            return LinkedInCallResult(status_code=None, headers={}, body_text="", transport_error="connection")
        safe_headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"authorization", "set-cookie", "cookie"}
        }
        return LinkedInCallResult(
            status_code=response.status_code,
            headers=safe_headers,
            body_text=response.text,
        )


def _safe_error_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return "oauth_error"
    if not isinstance(payload, dict):
        return "oauth_error"
    error = payload.get("error")
    if isinstance(error, str) and error.replace("_", "").isalnum() and len(error) <= 64:
        return error
    return "oauth_error"
