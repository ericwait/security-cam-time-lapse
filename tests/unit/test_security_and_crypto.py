import pytest

from app.security import (
    MIN_PASSWORD_LENGTH,
    SecretDecryptionError,
    decrypt_secret,
    derive_fernet,
    encrypt_secret,
    generate_session_token,
    hash_password,
    hash_session_token,
    scrub_secrets,
    slugify_camera_name,
    validate_iana_timezone,
    verify_password,
)


def test_password_hash_and_verify_roundtrip_uses_unique_salts() -> None:
    candidate = "correct-horse-battery-staple"
    first_hash = hash_password(candidate)
    second_hash = hash_password(candidate)

    assert first_hash != second_hash
    parts = first_hash.split("$")
    assert len(parts) == 6
    assert parts[:4] == ["scrypt", "16384", "8", "1"]
    assert len(parts[4]) == 32
    assert len(parts[5]) == 64

    assert verify_password(candidate, first_hash) is True
    assert verify_password(candidate, second_hash) is True
    assert verify_password("wrong-horse-battery-staple", first_hash) is False


def test_hash_password_rejects_short_passwords_without_echoing() -> None:
    assert MIN_PASSWORD_LENGTH == 8
    short_pw = "short7!"
    with pytest.raises(ValueError, match="at least 8 characters") as exc_info:
        hash_password(short_pw)

    assert short_pw not in str(exc_info.value)


def test_verify_password_rejects_tampered_digest_and_malformed_hashes() -> None:
    valid_hash = hash_password("valid-operator-pass")
    prefix, digest_hex = valid_hash.rsplit("$", 1)
    flipped_last_char = "0" if digest_hex[-1] != "0" else "1"
    tampered_hash = f"{prefix}${digest_hex[:-1]}{flipped_last_char}"

    assert verify_password("valid-operator-pass", tampered_hash) is False

    malformed_inputs = [
        "",
        "not-a-hash",
        "bcrypt$16384$8$1$00$00",
        "scrypt$notanint$8$1$aa$bb",
        "scrypt$1000$8$1$00112233445566778899aabbccddeeff$0011",
        "scrypt$0$8$1$00112233445566778899aabbccddeeff$0011",
        "scrypt$16384$0$1$00112233445566778899aabbccddeeff$0011",
        "scrypt$16384$8$0$00112233445566778899aabbccddeeff$0011",
        "scrypt$16384$8$1$zz$0011",
        "scrypt$16384$8$1$$0011",
        "scrypt$16384$8$1$0011$",
    ]
    for malformed in malformed_inputs:
        assert verify_password("valid-operator-pass", malformed) is False


def test_fernet_encrypt_decrypt_roundtrip_and_randomized_iv() -> None:
    app_key = "unit-test-dummy-secret-alpha-000000"
    camera_secret = "cam-dummy-pass!@#$%^&*()"

    token_one = encrypt_secret(app_key, camera_secret)
    token_two = encrypt_secret(app_key, camera_secret)

    assert token_one.startswith("gAAAAAB")
    assert token_two.startswith("gAAAAAB")
    assert token_one != token_two
    assert camera_secret not in token_one

    assert decrypt_secret(app_key, token_one) == camera_secret
    assert decrypt_secret(app_key, token_two) == camera_secret


def test_decrypt_secret_with_wrong_key_or_corrupt_token_does_not_leak_secrets() -> None:
    original_key = "unit-test-dummy-secret-origin-00000"
    wrong_key = "unit-test-dummy-secret-rotated-9999"
    camera_secret = "super-sensitive-cam-credential"

    ciphertext = encrypt_secret(original_key, camera_secret)

    with pytest.raises(SecretDecryptionError, match="APP_SECRET_KEY") as exc_info:
        decrypt_secret(wrong_key, ciphertext)

    rendered_err = f"{exc_info.value} {exc_info.value!r}"
    assert original_key not in rendered_err
    assert wrong_key not in rendered_err
    assert camera_secret not in rendered_err
    assert ciphertext not in rendered_err

    with pytest.raises(SecretDecryptionError):
        decrypt_secret(original_key, "not-a-valid-fernet-token")

    with pytest.raises(SecretDecryptionError):
        decrypt_secret(original_key, "")


def test_fernet_rejects_empty_key_or_empty_plaintext() -> None:
    with pytest.raises(ValueError, match="app_secret_key"):
        derive_fernet("")

    with pytest.raises(ValueError, match="plaintext"):
        encrypt_secret("unit-test-dummy-secret-alpha-000000", "")


def test_scrub_secrets_redacts_raw_urlencoded_and_uri_credentials() -> None:
    raw_secret = "s3cr!t/p@ss#1"
    encoded_msg = (
        "Capture failed for s3cr!t/p@ss#1 and query token=s3cr%21t%2Fp%40ss%231 "
        "on rtsp://admin:inlinePass123@192.0.2.10:554/h264Preview_01_main "
        "and https://camuser:webPass456@cam.example.internal/snapshot.jpg"
    )
    redacted = scrub_secrets(encoded_msg, [raw_secret, None, ""])

    assert raw_secret not in redacted
    assert "s3cr%21t%2Fp%40ss%231" not in redacted
    assert "inlinePass123" not in redacted
    assert "webPass456" not in redacted
    assert "rtsp://admin:[REDACTED]@192.0.2.10:554/h264Preview_01_main" in redacted
    assert "https://camuser:[REDACTED]@cam.example.internal/snapshot.jpg" in redacted


def test_session_token_generation_and_sha256_hashing() -> None:
    raw_one, hash_one = generate_session_token()
    raw_two, hash_two = generate_session_token()

    assert raw_one != raw_two
    assert hash_one != hash_two
    assert raw_one != hash_one
    assert len(raw_one) >= 40
    assert len(hash_one) == 64
    assert hash_session_token(raw_one) == hash_one


def test_validate_iana_timezone_accepts_canonical_zones_and_rejects_offsets() -> None:
    assert validate_iana_timezone("  America/Chicago ") == "America/Chicago"
    assert validate_iana_timezone("UTC") == "UTC"
    assert validate_iana_timezone("America/Port-au-Prince") == "America/Port-au-Prince"

    invalid_zones = [
        "",
        "   ",
        "-06:00",
        "+05:30",
        "UTC-6",
        "UTC+02:00",
        "GMT-5",
        "Etc/GMT+6",
        "EST",
        "Mars/Olympus",
        "/UTC",
        "America/../UTC",
    ]
    for bad_zone in invalid_zones:
        with pytest.raises(ValueError, match="Invalid IANA timezone"):
            validate_iana_timezone(bad_zone)


def test_slugify_camera_name_normalizes_and_rejects_empty_results() -> None:
    assert slugify_camera_name("  Front Porch #1 (4K)!  ") == "front-porch-1-4k"
    assert slugify_camera_name("Driveway---West_Cam") == "driveway-west-cam"

    for invalid_name in ["", "   ", "---", "!!!"]:
        with pytest.raises(ValueError, match="alphanumeric"):
            slugify_camera_name(invalid_name)
