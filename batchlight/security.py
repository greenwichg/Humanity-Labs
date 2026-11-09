"""Password hashing, opaque tokens and API keys."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # Argon2id with library defaults

COMMON_PASSWORDS = frozenset(
    """password password1 password123 12345678 123456789 1234567890 qwerty123 qwertyuiop iloveyou admin123
    welcome123 letmein123 abc12345 passw0rd monkey123 football1 baseball1 sunshine1 princess1 batchlight1""".split()
)
MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_problem(password: str, email: str | None = None) -> str | None:
    """Return a human-readable reason the password is unacceptable, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > 200:
        return "Use at most 200 characters."
    if password.lower() in COMMON_PASSWORDS or len(set(password)) < 4:
        return "This password is too easy to guess."
    if email and password.lower() == email.lower():
        return "Do not use your email address as your password."
    return None


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def new_api_key() -> tuple[str, str, str]:
    """Return (full_key, prefix, sha256 hash). The full key is shown once."""
    prefix = secrets.token_hex(4)
    full = f"bl_{prefix}_{secrets.token_urlsafe(24)}"
    return full, prefix, token_hash(full)


def parse_api_key(value: str) -> str | None:
    """Return the prefix of a well-formed API key, else None."""
    parts = value.split("_", 2)
    if len(parts) != 3 or parts[0] != "bl" or len(parts[1]) != 8 or len(parts[2]) < 20:
        return None
    return parts[1]
