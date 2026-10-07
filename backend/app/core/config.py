import re
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
_DEPRECATED_SCOPES = frozenset({"r_liteprofile", "r_emailaddress", "w_share"})
_CALLBACK_PATH = "/api/auth/linkedin/callback"

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_ROOT.parent
_ENV_FILES = tuple(
    path
    for path in (_REPO_ROOT / ".env", _BACKEND_ROOT / ".env")
    if path.is_file()
)


class Settings(BaseSettings):
    """Runtime configuration loaded from the environment and optional .env files."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES or None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(min_length=1)
    token_encryption_key: str = Field(min_length=1)
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    linkedin_redirect_uri: str = ""
    linkedin_scopes: str = "w_member_social"
    linkedin_version: str = "202609"
    frontend_origin: str = Field(
        default="http://localhost:5173",
        validation_alias=AliasChoices("FRONTEND_URL", "FRONTEND_ORIGIN"),
    )
    secret_key: str = ""
    oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    redis_url: str = "redis://redis:6379/0"
    celery_beat_interval_seconds: float = Field(default=15, ge=5, le=3600)

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value.removeprefix("postgresql://")
        return value

    @field_validator("token_encryption_key")
    @classmethod
    def _validate_encryption_key(cls, value: str) -> str:
        try:
            Fernet(value.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "TOKEN_ENCRYPTION_KEY must be a url-safe base64 32-byte Fernet key"
            ) from exc
        return value

    @field_validator("linkedin_redirect_uri")
    @classmethod
    def _validate_linkedin_redirect_uri(cls, value: str) -> str:
        if value == "":
            return value
        return _require_absolute_url(
            value,
            allowed_path=_CALLBACK_PATH,
            setting_name="LINKEDIN_REDIRECT_URI",
        )

    @field_validator("frontend_origin")
    @classmethod
    def _validate_frontend_origin(cls, value: str) -> str:
        return _require_absolute_url(value, allowed_path="", setting_name="FRONTEND_ORIGIN").rstrip("/")

    @field_validator("linkedin_scopes")
    @classmethod
    def _validate_linkedin_scopes(cls, value: str) -> str:
        scopes = value.split()
        if not scopes:
            raise ValueError("LINKEDIN_SCOPES must include at least one scope")
        if any(scope in _DEPRECATED_SCOPES for scope in scopes):
            raise ValueError("LINKEDIN_SCOPES contains a deprecated LinkedIn scope")
        if any(not scope.replace("_", "").isalnum() for scope in scopes):
            raise ValueError("LINKEDIN_SCOPES contains an invalid scope name")
        return " ".join(scopes)

    @field_validator("linkedin_version")
    @classmethod
    def _validate_linkedin_version(cls, value: str) -> str:
        if not re.fullmatch(r"\d{6}", value):
            raise ValueError("LINKEDIN_VERSION must be a YYYYMM value")
        return value

    @field_validator("redis_url")
    @classmethod
    def _validate_redis_url(cls, value: str) -> str:
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must start with redis:// or rediss://")
        return value


def _require_absolute_url(value: str, *, allowed_path: str, setting_name: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{setting_name} must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError(f"{setting_name} must not include user info or a fragment")
    if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS:
        raise ValueError(f"{setting_name} must use https except on localhost")
    path = parsed.path.rstrip("/")
    if allowed_path == "":
        if path not in {"", "/"}:
            raise ValueError(f"{setting_name} must not include a path")
    elif path != allowed_path:
        raise ValueError(f"{setting_name} must use the path {allowed_path}")
    return value


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Drop the cached settings. Used by tests after environment changes."""
    global _settings
    _settings = None
