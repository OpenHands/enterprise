"""Password-library compatibility and the native email identity boundary."""

import threading

import pytest
from fastapi_users.password import PasswordHelper

from server.auth import native_password
from server.auth.native_password import (
    NativeAuthError,
    hash_password,
    normalize_email,
    validate_password,
)

PASSWORD = ' A long Pássword with spaces 987! '


async def test_password_hashing_preserves_exact_password() -> None:
    password_hash = await hash_password(PASSWORD)
    assert password_hash.startswith('$argon2id$')
    helper = PasswordHelper()
    assert helper.verify_and_update(PASSWORD, password_hash) == (True, None)
    assert not helper.verify_and_update(PASSWORD.strip(), password_hash)[0]
    assert not helper.verify_and_update(PASSWORD.casefold(), password_hash)[0]


@pytest.mark.parametrize('password', ['x' * 15, 'passwordpassword', '🔐' * 1024])
def test_password_policy_uses_length_without_composition_rules(password: str) -> None:
    validate_password(password)


@pytest.mark.parametrize('password', ['', 'x' * 14, 'x' * 1025])
async def test_new_password_length_limits(password: str) -> None:
    with pytest.raises(NativeAuthError):
        await hash_password(password)


async def test_enrollment_hash_work_remains_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_password, '_HASH_CAPACITY', threading.BoundedSemaphore(0)
    )
    with pytest.raises(NativeAuthError) as error:
        await hash_password(PASSWORD)
    assert error.value.status_code == 503


@pytest.mark.parametrize(
    ('email', 'expected'),
    [
        (' Person@EXAMPLE.com ', 'person@example.com'),
        ('First.Last+Tag@example.com', 'first.last+tag@example.com'),
        ('Name@company.internal', 'name@company.internal'),
        ('Straße@example.com', 'strasse@example.com'),
    ],
)
def test_email_syntax_validation_preserves_case_insensitive_identity(
    email: str, expected: str
) -> None:
    assert normalize_email(email) == expected


@pytest.mark.parametrize(
    'email',
    [
        'person..name@example.com',
        'person.@example.com',
        'person@bad_domain.com',
        'person@example.com\nBcc:other@example.com',
        'person@localhost',
        'person@example.test',
        'person@corp.local',
        'person\u200b@example.com',
        'person@' + 'a' * 64 + '.com',
    ],
)
def test_email_validator_rejects_unsafe_or_invalid_addresses(email: str) -> None:
    with pytest.raises(NativeAuthError, match='Enter a valid email address'):
        normalize_email(email)


def test_email_casefold_expansion_cannot_exceed_storage_limit() -> None:
    email = '\u0390' * 110 + '@example.com'
    assert len(email) < 320 < len(email.casefold())
    with pytest.raises(NativeAuthError, match='Enter a valid email address'):
        normalize_email(email)


@pytest.mark.parametrize(
    ('first', 'second'),
    [
        ('person@xn--bcher-kva.de', 'person@bücher.de'),
        ('fo\u0301o@example.com', 'fóo@example.com'),
        ('first.last@example.com', 'firstlast@example.com'),
        ('person+tag@example.com', 'person@example.com'),
    ],
)
def test_email_validation_does_not_merge_established_identity_keys(
    first: str, second: str
) -> None:
    assert normalize_email(first) == first.casefold()
    assert normalize_email(second) == second.casefold()
    assert normalize_email(first) != normalize_email(second)
