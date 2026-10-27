"""Atomic shared-secret replacement through HTTP and real PostgreSQL storage."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.user_auth import get_user_id
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.routes.org_secrets import org_secrets_router
from storage.org_secrets_store import OrgSecretsStore


@pytest.fixture
async def rotation(monkeypatch, async_session_maker, create_org):
    monkeypatch.setattr(
        'storage.org_secrets_store.a_session_maker', async_session_maker
    )
    jwt = JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('test-key'), active=True)]
    )
    org = create_org()
    store = OrgSecretsStore(org.id, jwt)
    await store.create_shared('TOKEN', 'fake-old', 'metadata', 'synthetic-owner')
    role = MagicMock()
    role.name = 'owner'
    auth = AsyncMock(return_value=role)
    monkeypatch.setattr('server.auth.authorization.get_user_org_role', auth)
    factory = AsyncMock(side_effect=lambda org_id: OrgSecretsStore(org_id, jwt))
    monkeypatch.setattr(OrgSecretsStore, 'get_instance', factory)
    app = FastAPI()
    app.include_router(org_secrets_router)
    app.dependency_overrides[get_user_id] = lambda: str(uuid4())
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            store=store,
            client=client,
            app=app,
            auth=auth,
            role=role,
            factory=factory,
            jwt=jwt,
            base=f'/api/organizations/{org.id}/secrets/TOKEN',
        )


@pytest.mark.asyncio
async def test_http_replace_is_atomic_and_retryable(rotation):
    client, store = rotation.client, rotation.store
    before = await store.get_shared('TOKEN')
    response = client.get(rotation.base + '/version')
    assert response.status_code == 200
    version = response.json()['version']
    payload = {
        'expected_version': version,
        'request_id': str(uuid4()),
        'value': 'fake-new',
    }
    first = client.put(rotation.base + '/value', json=payload)
    assert first.status_code == 200
    assert first.json()['version'] != version
    assert client.put(rotation.base + '/value', json=payload).json() == first.json()
    after = await store.get_shared('TOKEN')
    assert after.id == before.id
    assert after.secret_name == before.secret_name
    assert after.description == before.description
    assert after.keycloak_user_id == before.keycloak_user_id
    assert store._jwt_svc.decrypt_value(after.secret_value) == 'fake-new'
    assert after.secret_value != 'fake-new'
    assert 'fake-new' not in first.text
    assert client.get(rotation.base + '/version').json() == first.json()


def test_value_endpoint_requires_existing_version(rotation):
    response = rotation.client.put(
        rotation.base + '/value', json={'value': 'fake-new', 'request_id': str(uuid4())}
    )
    assert response.status_code == 422
    assert 'fake-new' not in response.text


@pytest.mark.asyncio
async def test_conflicting_requests_never_change_the_value(rotation, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    version = rotation.client.get(rotation.base + '/version').json()['version']
    request_id = str(uuid4())
    payload = {
        'expected_version': version,
        'request_id': request_id,
        'value': 'fake-new',
    }
    first = rotation.client.put(rotation.base + '/value', json=payload)
    assert first.status_code == 200
    for body in [
        {**payload, 'value': 'fake-conflict-canary'},
        {**payload, 'request_id': str(uuid4()), 'value': 'fake-conflict-canary'},
        {
            **payload,
            'expected_version': first.json()['version'],
            'value': 'fake-conflict-canary',
        },
    ]:
        response = rotation.client.put(rotation.base + '/value', json=body)
        assert response.status_code == 409
        assert 'fake-conflict-canary' not in response.text
    assert 'fake-conflict-canary' not in caplog.text
    assert (await rotation.store.load_shared_values())[
        'TOKEN'
    ].secret.get_secret_value() == 'fake-new'
    assert rotation.client.get(rotation.base + '/version').json() == first.json()


@pytest.mark.parametrize(
    'role,expected', [('owner', 200), ('admin', 200), ('member', 403), (None, 403)]
)
def test_replacement_requires_manage_org_secrets(rotation, role, expected):
    if role is None:
        rotation.auth.return_value = None
    else:
        rotation.role.name = role
    response = rotation.client.put(
        rotation.base + '/value',
        json={
            'expected_version': str(uuid4()),
            'request_id': str(uuid4()),
            'value': 'fake-auth-canary',
        },
    )
    # Authorized writers reach the stale-version check; unauthorized ones do not.
    assert response.status_code == (409 if expected == 200 else expected)
    if expected == 403:
        rotation.factory.assert_not_awaited()
    assert 'fake-auth-canary' not in response.text


def test_members_can_read_only_the_revision(rotation):
    rotation.role.name = 'member'
    response = rotation.client.get(rotation.base + '/version')
    assert response.status_code == 200
    assert set(response.json()) == {'version'}
    UUID(response.json()['version'])
    assert 'fake-old' not in response.text


@pytest.mark.parametrize('method', ['get', 'put'])
def test_nonmember_cannot_discover_or_replace_another_org_secret(rotation, method):
    rotation.auth.return_value = None
    kwargs = (
        {}
        if method == 'get'
        else {
            'json': {
                'expected_version': str(uuid4()),
                'request_id': str(uuid4()),
                'value': 'fake-forbidden',
            }
        }
    )
    response = getattr(rotation.client, method)(
        rotation.base + ('/version' if method == 'get' else '/value'), **kwargs
    )
    assert response.status_code == 403
    rotation.factory.assert_not_awaited()


@pytest.mark.parametrize('method', ['get', 'put'])
def test_missing_secret_is_never_created_by_replacement(rotation, method):
    url = rotation.base.replace('/TOKEN', '/MISSING')
    kwargs = (
        {}
        if method == 'get'
        else {
            'json': {
                'expected_version': str(uuid4()),
                'request_id': str(uuid4()),
                'value': 'fake-new',
            }
        }
    )
    response = getattr(rotation.client, method)(
        url + ('/version' if method == 'get' else '/value'), **kwargs
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    'payload',
    [
        {'value': 'fake-validation-canary'},
        {
            'value': {'nested': 'fake-validation-canary'},
            'expected_version': str(uuid4()),
            'request_id': str(uuid4()),
        },
        {
            'value': 'fake-validation-canary',
            'expected_version': 'bad-version',
            'request_id': str(uuid4()),
        },
        {
            'value': 'fake-validation-canary',
            'expected_version': str(uuid4()),
            'request_id': 'bad-request-id',
        },
        {
            'value': 'fake-validation-canary',
            'expected_version': str(uuid4()),
            'request_id': str(uuid4()),
            'description': 'ignored?',
        },
    ],
)
def test_validation_does_not_reflect_write_only_values(rotation, payload, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    response = rotation.client.put(rotation.base + '/value', json=payload)
    assert response.status_code == 422
    assert 'fake-validation-canary' not in response.text
    assert 'fake-validation-canary' not in caplog.text
    assert all(
        set(error) == {'type', 'loc', 'msg'} for error in response.json()['detail']
    )
    rotation.factory.assert_not_awaited()


@pytest.mark.parametrize(
    'content_type,body',
    [
        ('text/plain', '{"value":"fake-raw-canary"}'),
        ('application/json', '{"value":"fake-raw-canary"'),
    ],
)
def test_raw_invalid_request_does_not_reflect_values(
    rotation, content_type, body, caplog
):
    import logging

    caplog.set_level(logging.DEBUG)
    response = rotation.client.put(
        rotation.base + '/value', content=body, headers={'Content-Type': content_type}
    )
    assert response.status_code == 422
    assert 'fake-raw-canary' not in response.text
    assert 'fake-raw-canary' not in caplog.text
    rotation.factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_path_org_is_not_replaced_by_body_or_other_org_version(
    rotation, create_org
):
    other = OrgSecretsStore(create_org().id, rotation.jwt)
    await other.create_shared('TOKEN', 'fake-other-org', None, 'other-owner')
    version = rotation.client.get(rotation.base + '/version').json()['version']
    url = f'/api/organizations/{other.org_id}/secrets/TOKEN/value'
    response = rotation.client.put(
        url,
        json={
            'value': 'fake-cross-org',
            'expected_version': version,
            'request_id': str(uuid4()),
        },
    )
    assert response.status_code == 409
    assert (await other.load_shared_values())[
        'TOKEN'
    ].secret.get_secret_value() == 'fake-other-org'
    assert (await rotation.store.load_shared_values())[
        'TOKEN'
    ].secret.get_secret_value() == 'fake-old'


def test_replacement_body_is_write_only_and_metadata_api_is_preserved(rotation):
    schema = rotation.app.openapi()['components']['schemas'][
        'OrgSecretValueReplacement'
    ]
    assert schema['properties']['value']['writeOnly'] is True
    assert schema['additionalProperties'] is False
    assert set(schema['required']) == {'value', 'expected_version', 'request_id'}
    response = rotation.client.get(rotation.base.rsplit('/', 1)[0])
    assert response.status_code == 200
    assert 'fake-old' not in response.text
    assert set(response.json()['items'][0]) == {'name', 'description', 'scope'}


@pytest.mark.parametrize('method', ['get', 'put'])
def test_anonymous_requests_cannot_read_revisions_or_write_values(rotation, method):
    rotation.app.dependency_overrides[get_user_id] = lambda: None
    kwargs = (
        {}
        if method == 'get'
        else {
            'json': {
                'expected_version': str(uuid4()),
                'request_id': str(uuid4()),
                'value': 'fake-anonymous',
            }
        }
    )
    response = getattr(rotation.client, method)(
        rotation.base + ('/version' if method == 'get' else '/value'), **kwargs
    )
    assert response.status_code == 401
    rotation.factory.assert_not_awaited()


@pytest.mark.parametrize('method', ['get', 'put'])
def test_org_bound_key_cannot_target_another_org(rotation, monkeypatch, method):
    monkeypatch.setattr(
        'server.auth.authorization.get_api_key_org_id_from_request',
        AsyncMock(return_value=uuid4()),
    )
    kwargs = (
        {}
        if method == 'get'
        else {
            'json': {
                'expected_version': str(uuid4()),
                'request_id': str(uuid4()),
                'value': 'fake-wrong-org',
            }
        }
    )
    response = getattr(rotation.client, method)(
        rotation.base + ('/version' if method == 'get' else '/value'), **kwargs
    )
    assert response.status_code == 403
    rotation.factory.assert_not_awaited()


def test_extra_field_names_cannot_reflect_credentials(rotation):
    response = rotation.client.put(
        rotation.base + '/value',
        json={
            'value': 'fake-new',
            'expected_version': str(uuid4()),
            'request_id': str(uuid4()),
            'fake-credential-as-key': 1,
        },
    )
    assert response.status_code == 422
    assert 'fake-credential-as-key' not in response.text
    assert response.json()['detail'][0]['loc'] == ['body']
    rotation.factory.assert_not_awaited()


def test_invalid_unicode_value_is_a_safe_client_error(rotation):
    import json

    payload = {
        'value': '\ud800',
        'expected_version': str(uuid4()),
        'request_id': str(uuid4()),
    }
    response = rotation.client.put(
        rotation.base + '/value',
        content=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
    )
    assert response.status_code == 422
    assert 'valid UTF-8' in response.text
    rotation.factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_metadata_edit_returns_conflict(rotation, async_session_maker):
    from storage.stored_custom_secrets import StoredCustomSecrets

    async with async_session_maker() as session:
        session.add(
            StoredCustomSecrets(
                org_id=rotation.store.org_id,
                secret_name='TOKEN',
                secret_value=rotation.jwt.encrypt_value('fake-duplicate'),
                is_org_shared=True,
            )
        )
        await session.commit()
    response = rotation.client.put(
        rotation.base, json={'description': 'should not change'}
    )
    assert response.status_code == 409
    response = rotation.client.get(rotation.base + '/version')
    assert response.status_code == 409
    response = rotation.client.put(
        rotation.base + '/value',
        json={
            'value': 'fake-new',
            'expected_version': str(uuid4()),
            'request_id': str(uuid4()),
        },
    )
    assert response.status_code == 409
