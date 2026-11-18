import base64
import hashlib
import hmac
import re
import secrets
import urllib.parse
from collections.abc import Sequence
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MIN_PASSWORD_LENGTH: Final[int] = 8

_SCRYPT_SCHEME: Final[str] = "scrypt"
_SCRYPT_N: Final[int] = 2**14
_SCRYPT_R: Final[int] = 8
_SCRYPT_P: Final[int] = 1
_SCRYPT_SALT_BYTES: Final[int] = 16
_SCRYPT_DKLEN: Final[int] = 32
_SCRYPT_MAX_N: Final[int] = 2**20
_SCRYPT_MAX_R: Final[int] = 32
_SCRYPT_MAX_P: Final[int] = 16

_HKDF_SALT: Final[bytes] = b"security-cam-time-lapse-camera-secrets-v1"
_HKDF_INFO: Final[bytes] = b"fernet-key"

_REDACTED_PLACEHOLDER: Final[str] = "[REDACTED]"
_URI_CREDENTIALS_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)((?:rtsp|rtsps|http|https)://[^/\s:@]+:)([^@\s/]+)(@)"
)
_FIXED_OFFSET_TZ_RE: Final[re.Pattern[str]] = re.compile(r"(?i)^(?:[+-]|(?:utc|gmt)[+-]|etc/gmt)")
_NON_SLUG_CHARS_RE: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")


class SecretDecryptionError(RuntimeError):
    """Raised when camera credentials cannot be decrypted with APP_SECRET_KEY."""


def hash_password(password: str) -> str:
    """Hash a plaintext user password using scrypt with a random 16-byte salt."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.")

    salt = secrets.token_bytes(_SCRYPT_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"{_SCRYPT_SCHEME}${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify a plaintext password against an encoded scrypt hash without raising."""
    parts = encoded_hash.split("$")
    if len(parts) != 6 or parts[0] != _SCRYPT_SCHEME:
        return False

    try:
        n = int(parts[1])
        r = int(parts[2])
        p = int(parts[3])
        salt = bytes.fromhex(parts[4])
        expected_digest = bytes.fromhex(parts[5])
    except ValueError:
        return False

    if (
        n <= 1
        or n > _SCRYPT_MAX_N
        or (n & (n - 1)) != 0
        or r <= 0
        or r > _SCRYPT_MAX_R
        or p <= 0
        or p > _SCRYPT_MAX_P
        or not salt
        or not expected_digest
    ):
        return False

    try:
        computed_digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected_digest),
        )
    except ValueError:
        return False

    return hmac.compare_digest(computed_digest, expected_digest)


def derive_fernet(app_secret_key: str) -> Fernet:
    """Derive a deterministic Fernet cipher from APP_SECRET_KEY via HKDF-SHA256."""
    if not app_secret_key:
        raise ValueError("app_secret_key must not be empty.")

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    derived_key = hkdf.derive(app_secret_key.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(derived_key))


def encrypt_secret(app_secret_key: str, plaintext: str) -> str:
    """Encrypt a camera credential secret into a UTF-8 Fernet token string."""
    if not plaintext:
        raise ValueError("Secret plaintext must not be empty.")

    fernet = derive_fernet(app_secret_key)
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(app_secret_key: str, ciphertext: str) -> str:
    """Decrypt a Fernet camera credential token without leaking keys or tokens."""
    fernet = derive_fernet(app_secret_key)
    if not ciphertext:
        raise SecretDecryptionError(
            "Failed to decrypt camera credentials with current APP_SECRET_KEY."
        )

    try:
        plaintext_bytes = fernet.decrypt(ciphertext.encode("utf-8"))
    except InvalidToken:
        raise SecretDecryptionError(
            "Failed to decrypt camera credentials with current APP_SECRET_KEY."
        ) from None

    try:
        return plaintext_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise SecretDecryptionError(
            "Failed to decrypt camera credentials with current APP_SECRET_KEY."
        ) from None


def scrub_secrets(message: str, secrets_to_redact: Sequence[str | None] = ()) -> str:
    """Redact explicit secret values, URL-encoded variants, and URI passwords."""
    candidates: set[str] = set()
    for secret in secrets_to_redact:
        if secret:
            candidates.add(secret)
            candidates.add(urllib.parse.quote(secret, safe=""))

    redacted = message
    for candidate in sorted(candidates, key=len, reverse=True):
        redacted = redacted.replace(candidate, _REDACTED_PLACEHOLDER)

    return _URI_CREDENTIALS_RE.sub(rf"\1{_REDACTED_PLACEHOLDER}\3", redacted)


def hash_session_token(raw_token: str) -> str:
    """Return the hex-encoded SHA-256 digest of a raw session token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_session_token() -> tuple[str, str]:
    """Generate a URL-safe session token and its SHA-256 hex digest."""
    raw_token = secrets.token_urlsafe(32)
    return raw_token, hash_session_token(raw_token)


def validate_iana_timezone(tz_name: str) -> str:
    """Validate and normalize a canonical IANA timezone name, rejecting fixed offsets."""
    cleaned = tz_name.strip()
    if (
        not cleaned
        or cleaned.startswith("/")
        or ".." in cleaned
        or _FIXED_OFFSET_TZ_RE.match(cleaned) is not None
        or (cleaned != "UTC" and "/" not in cleaned)
    ):
        raise ValueError(f"Invalid IANA timezone: {tz_name!r}")

    try:
        ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Invalid IANA timezone: {tz_name!r}") from None

    return cleaned


def slugify_camera_name(name: str) -> str:
    """Convert a human-readable camera name into a lowercase URL-safe slug."""
    slug = _NON_SLUG_CHARS_RE.sub("-", name.lower()).strip("-")
    if not slug:
        raise ValueError("Camera name must contain at least one ASCII alphanumeric character.")
    return slug
