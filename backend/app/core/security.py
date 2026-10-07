from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class TokenStorageError(ValueError):
    """A token cannot be stored or read without exposing secret material."""


def _fernet() -> Fernet:
    return Fernet(get_settings().token_encryption_key.encode("utf-8"))


def encrypt_secret(plaintext: str) -> str:
    """Encrypt an OAuth token for storage. The plaintext is never logged."""
    if not isinstance(plaintext, str) or plaintext == "":
        raise TokenStorageError("OAuth token must be a non-empty string")
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a stored OAuth token. Failures do not include the token value."""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, TypeError, ValueError, UnicodeError) as exc:
        raise TokenStorageError("Stored OAuth token could not be decrypted") from exc
