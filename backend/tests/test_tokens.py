from __future__ import annotations

import logging

from app.core.logging import configure_logging, redact_text
from app.core.security import TokenStorageError, decrypt_secret, encrypt_secret
import pytest


def test_encrypt_round_trip_and_ciphertext_differs() -> None:
    plaintext = "linkedin-access-token-do-not-store-plaintext"
    ciphertext = encrypt_secret(plaintext)
    assert ciphertext != plaintext
    assert plaintext not in ciphertext
    assert decrypt_secret(ciphertext) == plaintext


def test_plaintext_and_empty_values_are_rejected() -> None:
    with pytest.raises(TokenStorageError):
        decrypt_secret("not-a-fernet-token")
    with pytest.raises(TokenStorageError):
        encrypt_secret("")


def test_logs_never_contain_tokens(caplog: pytest.LogCaptureFixture) -> None:
    plaintext = "linkedin-access-token-do-not-store-plaintext"
    ciphertext = encrypt_secret(plaintext)
    configure_logging()
    logger = logging.getLogger("app.tests.tokens")

    with caplog.at_level(logging.INFO):
        logger.info("access_token=%s", plaintext)
        logger.info("Authorization: Bearer %s", plaintext)
        logger.info("stored ciphertext %s", ciphertext)
        encrypt_secret(plaintext)
        decrypt_secret(ciphertext)

    rendered = caplog.text
    assert plaintext not in rendered
    assert ciphertext not in rendered
    assert "[REDACTED]" in rendered
    assert "access_token=[REDACTED]" in redact_text(f"access_token={plaintext}")
