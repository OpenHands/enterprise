"""Verification of the worklog run token the automation service mints.

``VECTOR_*`` is the contract with OpenHands/automation#726, which commits the
same key, claims and token in ``tests/test_worklog_token.py``. If the two ever
differ, OpenHands/enterprise#1297 is authoritative.
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException

from server.auth.worklog_token import (
    derive_worklog_signing_key,
    require_worklog_run_token,
    verify_worklog_token,
)

VECTOR_SERVICE_KEY = 'worklog-contract-service-key'
VECTOR_CLAIMS = {
    'aud': 'worklog',
    'org_id': '11111111-1111-4111-8111-111111111111',
    'factory_id': '22222222-2222-4222-8222-222222222222',
    'automation_id': '33333333-3333-4333-8333-333333333333',
    'automation_name': 'Release orchestrator',
    'run_id': '44444444-4444-4444-8444-444444444444',
    'iat': 1767225600,
    'exp': 1767312000,
}
VECTOR_TOKEN = (
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.'
    'eyJhdWQiOiJ3b3JrbG9nIiwib3JnX2lkIjoiMTExMTExMTEtMTExMS00MTExLTgxMTEtMTExMTEx'
    'MTExMTExIiwiZmFjdG9yeV9pZCI6IjIyMjIyMjIyLTIyMjItNDIyMi04MjIyLTIyMjIyMjIyMjIy'
    'MiIsImF1dG9tYXRpb25faWQiOiIzMzMzMzMzMy0zMzMzLTQzMzMtODMzMy0zMzMzMzMzMzMzMzMi'
    'LCJhdXRvbWF0aW9uX25hbWUiOiJSZWxlYXNlIG9yY2hlc3RyYXRvciIsInJ1bl9pZCI6IjQ0NDQ0'
    'NDQ0LTQ0NDQtNDQ0NC04NDQ0LTQ0NDQ0NDQ0NDQ0NCIsImlhdCI6MTc2NzIyNTYwMCwiZXhwIjox'
    'NzY3MzEyMDAwfQ.'
    'uf3R42EbWDlV0HrrVke_eJzQvW8vBFvCORKeBxFfua8'
)


@pytest.fixture(autouse=True)
def service_key(monkeypatch):
    monkeypatch.setenv('AUTOMATIONS_SERVICE_KEY', VECTOR_SERVICE_KEY)


def _mint(key: str = VECTOR_SERVICE_KEY, **overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        **VECTOR_CLAIMS,
        'iat': int(now.timestamp()),
        'exp': int((now + timedelta(hours=24)).timestamp()),
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, derive_worklog_signing_key(key), algorithm='HS256')


def test_the_contract_vector_verifies_with_the_derived_key():
    claims = jwt.decode(
        VECTOR_TOKEN,
        derive_worklog_signing_key(VECTOR_SERVICE_KEY),
        algorithms=['HS256'],
        audience='worklog',
        options={'verify_exp': False},
    )

    assert claims == VECTOR_CLAIMS


def test_the_contract_vector_is_expired_now():
    with pytest.raises(HTTPException) as exc:
        verify_worklog_token(VECTOR_TOKEN)

    assert exc.value.status_code == 401
    assert 'expired' in exc.value.detail


def test_the_signing_key_is_hkdf_of_the_service_key_not_the_key_itself():
    key = derive_worklog_signing_key(VECTOR_SERVICE_KEY)

    assert len(key) == 32
    assert key != VECTOR_SERVICE_KEY.encode()
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            VECTOR_TOKEN,
            VECTOR_SERVICE_KEY,
            algorithms=['HS256'],
            options={'verify_exp': False, 'verify_aud': False},
        )


def test_a_fresh_token_verifies():
    claims = verify_worklog_token(_mint())

    assert str(claims.factory_id) == VECTOR_CLAIMS['factory_id']


@pytest.mark.parametrize(
    'token',
    [
        pytest.param(lambda: _mint(key='another-key'), id='wrong-key'),
        pytest.param(lambda: _mint(aud='kv'), id='wrong-audience'),
        pytest.param(lambda: _mint(aud=None), id='no-audience'),
        pytest.param(
            lambda: _mint(exp=int(datetime.now(timezone.utc).timestamp()) - 5),
            id='expired',
        ),
        pytest.param(lambda: _mint(exp=None), id='no-expiry'),
        pytest.param(lambda: _mint(factory_id=None), id='no-factory'),
        pytest.param(lambda: _mint(org_id='not-a-uuid'), id='bad-org'),
        pytest.param(lambda: 'not.a.token', id='garbage'),
        pytest.param(
            lambda: jwt.encode(
                {**VECTOR_CLAIMS, 'exp': 4_000_000_000}, key=None, algorithm='none'
            ),
            id='alg-none',
        ),
    ],
)
def test_a_bad_token_is_401(token):
    with pytest.raises(HTTPException) as exc:
        verify_worklog_token(token())

    assert exc.value.status_code == 401


def test_without_the_service_key_every_token_is_503(monkeypatch):
    token = _mint()
    monkeypatch.delenv('AUTOMATIONS_SERVICE_KEY')

    with pytest.raises(HTTPException) as exc:
        verify_worklog_token(token)

    assert exc.value.status_code == 503


def test_a_long_automation_name_is_cut_to_256_characters():
    claims = verify_worklog_token(_mint(automation_name='n' * 1000))

    assert claims.automation_name == 'n' * 256


def test_the_run_id_and_ids_are_uuids():
    claims = verify_worklog_token(_mint())

    assert isinstance(claims.run_id, uuid.UUID)
    assert isinstance(claims.org_id, uuid.UUID)


@pytest.mark.parametrize(
    'header',
    [None, '', 'Bearer', 'Bearer  ', 'Basic abc', 'abc'],
)
async def test_the_header_must_be_a_bearer_token(header):
    with pytest.raises(HTTPException) as exc:
        await require_worklog_run_token(header)

    assert exc.value.status_code == 401


async def test_a_bearer_header_yields_the_claims():
    claims = await require_worklog_run_token(f'Bearer {_mint()}')

    assert str(claims.run_id) == VECTOR_CLAIMS['run_id']
