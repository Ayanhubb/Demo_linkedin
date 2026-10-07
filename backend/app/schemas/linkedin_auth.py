from pydantic import BaseModel, ConfigDict, Field, SecretStr


class OAuthErrorResponse(BaseModel):
    """Public OAuth error. It never includes tokens or the client secret."""

    error: str
    message: str


class LinkedInAccessToken(BaseModel):
    """Token endpoint payload. Secret fields stay masked if the model is printed."""

    model_config = ConfigDict(extra="ignore")

    access_token: SecretStr
    expires_in: int = Field(gt=0)
    refresh_token: SecretStr | None = None
    scope: str | None = None


class LinkedInMemberIdentity(BaseModel):
    """Current member identity from the OpenID Connect userinfo endpoint."""

    model_config = ConfigDict(extra="ignore")

    sub: str = Field(min_length=1, max_length=255)
    name: str | None = None
