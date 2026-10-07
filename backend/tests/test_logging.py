import json
import logging

from app.core.logging import configure_logging, log_event, redact_text


def test_redacts_authorization_code_and_tokens() -> None:
    rendered = redact_text(
        "code=oauth-code-value access_token=plain-token refresh_token=other client_secret=hidden Bearer abc.def"
    )
    assert "oauth-code-value" not in rendered
    assert "plain-token" not in rendered
    assert "other" not in rendered
    assert "hidden" not in rendered
    assert "abc.def" not in rendered
    assert "[REDACTED]" in rendered
    wrapped = redact_text(
        '{"message": "callback ?code=oauth-code-value", "level": "WARNING"}'
    )
    payload = json.loads(wrapped)
    assert payload["level"] == "WARNING"
    assert "oauth-code-value" not in wrapped
    safe = redact_text(
        '{"message": "linkedin_post_failed error_code=invalid_token", "level": "WARNING"}'
    )
    assert json.loads(safe)["level"] == "WARNING"
    assert "invalid_token" in safe


def test_log_event_includes_the_required_fields(caplog) -> None:
    configure_logging()
    logger = logging.getLogger("test.events")
    with caplog.at_level(logging.INFO):
        log_event(
            logger,
            "linkedin_post_publish",
            post_id="123",
            user_id="user-1",
            status="success",
            attempt_count=1,
            duration=0.2,
        )
    payload = json.loads(caplog.records[-1].getMessage())
    assert payload["event"] == "linkedin_post_publish"
    assert payload["post_id"] == "123"
    assert payload["user_id"] == "user-1"
    assert payload["status"] == "success"
    assert payload["attempt_count"] == 1
    assert payload["attempt"] == 1
    assert payload["duration"] == 0.2
    assert "request_id" in payload
