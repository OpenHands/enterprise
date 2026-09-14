"""SAML browser/worker boundaries with real signatures and migrated PostgreSQL."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Unpack
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import func, select, text

from server.auth import saml_config
from server.auth.native_password import NativeAuthError
from server.auth.native_session import csrf_for_token, digest_token
from server.auth.native_types import SessionFactory
from server.auth.saml_config import SamlSettings
from server.routes import native_auth, native_saml
from server.services import native_saml_service
from server.services.native_auth_service import NativeLogin
from server.services.native_saml_service import NativeSamlService
from storage.native_auth import BrowserSession, ExternalIdentity, PasswordCredential
from storage.native_saml import SamlReplay, SamlTransaction
from tests.unit.server.auth.native_test_types import (
    NativeRuntime,
    SamlRuntime,
    SamlStartBody,
    SigningMaterial,
    XmlElement,
    present,
)
from tests.unit.server.auth.test_native_runtime import ORIGIN, native_app
from tests.unit.server.auth.test_native_saml_protocol import (
    _change,
    _time,
    signed_response,
)

pytest_plugins = [
    'tests.unit.server.auth.test_native_runtime',
    'tests.unit.server.auth.test_native_saml_protocol',
]


@pytest.fixture
def saml_runtime(
    native_runtime: NativeRuntime,
    saml_settings: SamlSettings,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> SamlRuntime:
    service, login, _ = native_runtime
    protocol = NativeSamlService(async_session_maker, saml_settings)
    monkeypatch.setattr(saml_config, 'get_saml_settings', lambda: saml_settings)
    monkeypatch.setattr(native_saml_service, 'get_saml_settings', lambda: saml_settings)
    monkeypatch.setattr(native_auth, 'get_native_auth_service', lambda: service)
    monkeypatch.setattr(native_saml, 'get_native_auth_service', lambda: service)
    monkeypatch.setattr(native_saml, 'get_native_saml_service', lambda: protocol)
    app = native_app()
    app.include_router(native_auth.native_auth_router)
    app.include_router(native_saml.native_saml_router)
    return app, protocol, service, login


async def begin_browser(
    client: httpx.AsyncClient, **body: Unpack[SamlStartBody]
) -> str:
    csrf = (await client.get('/api/auth/csrf')).json()['csrf_token']
    response = await client.post(
        '/api/auth/saml/start',
        json=body,
        headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf},
    )
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()['redirect_to']).query)
    return query['RelayState'][0]


async def transaction_for(protocol: NativeSamlService, relay: str) -> SamlTransaction:
    async with protocol.sessions() as session:
        return present(
            await session.scalar(
                select(SamlTransaction).where(
                    SamlTransaction.relay_digest == digest_token(relay, 'saml-relay')
                )
            )
        )


async def complete_browser(client: httpx.AsyncClient) -> httpx.Response:
    csrf = (await client.get('/api/auth/csrf')).json()['csrf_token']
    return await client.post(
        '/api/auth/saml/complete',
        json={},
        headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('previous_session', [None, 'valid', 'stale'])
async def test_cross_site_acs_requires_original_browser_completion(
    saml_runtime: SamlRuntime,
    saml_settings: SamlSettings,
    signing_material: SigningMaterial,
    previous_session: str | None,
) -> None:
    app, protocol, service, existing = saml_runtime
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as browser:
        if previous_session:
            browser.cookies.set(
                'openhands_session',
                existing.token if previous_session == 'valid' else 'stale-session',
                domain='native.example.com',
                path='/',
            )
        relay = await begin_browser(browser, return_path='/settings/integrations')
        transaction = await transaction_for(protocol, relay)
        encoded = signed_response(
            saml_settings, signing_material, transaction.request_id
        )
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as foreign:
            acs = await foreign.post(
                '/api/auth/saml/acs',
                data={'RelayState': relay, 'SAMLResponse': encoded},
                headers={'Origin': 'https://idp.example.test'},
            )
            assert acs.status_code == 303
            assert acs.headers['location'] == f'{ORIGIN}/auth/saml/complete'
            assert 'set-cookie' not in acs.headers
            assert (await complete_browser(foreign)).status_code == 400
            replay = await foreign.post(
                '/api/auth/saml/acs',
                data={'RelayState': relay, 'SAMLResponse': encoded},
            )
            assert replay.headers['location'].endswith(
                '/login?sso_error=invalid_response'
            )
        no_proof = await browser.post('/api/auth/saml/complete', json={})
        assert no_proof.status_code == 403
        completed = await complete_browser(browser)
        assert completed.status_code == 200, completed.text
        assert completed.json()['redirect_to'].startswith('/accept-tos?')
        principal = await service.authenticate_session(
            browser.cookies['openhands_session']
        )
        assert principal and principal.email == 'saml@example.test'
        assert principal.account_id != existing.principal.account_id
        async with protocol.sessions() as session:
            assert await session.get(PasswordCredential, principal.account_id) is None
            stored = await session.scalar(
                text('SELECT verified_claims FROM saml_transaction WHERE id=:id'),
                {'id': transaction.id},
            )
            assert stored is None
        assert (await complete_browser(browser)).status_code == 400


@pytest.mark.asyncio
async def test_explicit_link_revalidates_original_session(
    saml_runtime: SamlRuntime,
    saml_settings: SamlSettings,
    signing_material: SigningMaterial,
) -> None:
    app, protocol, service, existing = saml_runtime
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as browser:
        browser.cookies.set(
            'openhands_session', existing.token, domain='native.example.com', path='/'
        )
        relay = await begin_browser(browser, link=True, return_path='/settings/user')
        transaction = await transaction_for(protocol, relay)
        assert transaction.link_account_id == existing.principal.account_id
        await protocol.accept_response(
            relay,
            signed_response(
                saml_settings,
                signing_material,
                transaction.request_id,
                email=existing.principal.email,
            ),
        )
        result = await complete_browser(browser)
        assert result.status_code == 200, result.text
        principal = await service.authenticate_session(
            browser.cookies['openhands_session']
        )
        assert present(principal).account_id == existing.principal.account_id
        assert await service.authenticate_session(existing.token) is None
        async with protocol.sessions() as session:
            assert (
                await session.get(PasswordCredential, present(principal).account_id)
                is not None
            )
            assert await session.scalar(
                select(ExternalIdentity.id).where(
                    ExternalIdentity.account_id == present(principal).account_id
                )
            )


@pytest.mark.asyncio
async def test_equal_email_does_not_link_and_wrong_session_cannot_link(
    saml_runtime: SamlRuntime,
    saml_settings: SamlSettings,
    signing_material: SigningMaterial,
) -> None:
    _, protocol, service, existing = saml_runtime
    for link in (False, True):
        location, browser = await protocol.start(
            link=link, session_token=existing.token
        )
        relay = parse_qs(urlsplit(location).query)['RelayState'][0]
        transaction = await transaction_for(protocol, relay)
        await protocol.accept_response(
            relay,
            signed_response(
                saml_settings,
                signing_material,
                transaction.request_id,
                email=existing.principal.email,
            ),
        )
        if link:
            await service.revoke_session(existing.token)
        with pytest.raises(NativeAuthError):
            await protocol.complete(browser, existing.token if link else None)
    async with protocol.sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ExternalIdentity))
            == 0
        )


@pytest.mark.asyncio
async def test_cross_worker_single_consumption_and_replay_ids(
    saml_runtime: SamlRuntime,
    saml_settings: SamlSettings,
    signing_material: SigningMaterial,
) -> None:
    _, first, service, _ = saml_runtime
    second = NativeSamlService(first.sessions, saml_settings)
    location, browser = await first.start()
    relay = parse_qs(urlsplit(location).query)['RelayState'][0]
    transaction = await transaction_for(first, relay)
    encoded = signed_response(
        saml_settings,
        signing_material,
        transaction.request_id,
        assertion_id='_stable_assertion',
    )
    await first.accept_response(relay, encoded)
    results = await asyncio.gather(
        first.complete(browser, None),
        second.complete(browser, None),
        return_exceptions=True,
    )
    assert sum(isinstance(result, NativeAuthError) for result in results) == 1
    login = next(result for result in results if not isinstance(result, BaseException))
    assert await service.authenticate_session(login.token)
    async with first.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(BrowserSession)
                .where(BrowserSession.auth_method == 'saml')
            )
            == 1
        )
        assert await session.scalar(select(func.count()).select_from(SamlReplay)) == 2
    other_location, _ = await second.start()
    other_relay = parse_qs(urlsplit(other_location).query)['RelayState'][0]
    other_transaction = await transaction_for(second, other_relay)
    with pytest.raises(NativeAuthError):
        await second.accept_response(
            other_relay,
            signed_response(
                saml_settings,
                signing_material,
                other_transaction.request_id,
                assertion_id='_stable_assertion',
            ),
        )


@pytest.mark.asyncio
async def test_no_unsolicited_acs_wrong_method_or_unbounded_post(
    saml_runtime: SamlRuntime,
) -> None:
    app, _, _, existing = saml_runtime
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        for data in (
            {'RelayState': 'unknown', 'SAMLResponse': 'encoded'},
            {'SAMLResponse': 'encoded'},
            {'SAMLResponse': 'x' * (512 * 1024 + 1), 'RelayState': 'state'},
        ):
            response = await client.post('/api/auth/saml/acs', data=data)
            assert response.status_code == 303
            assert response.headers['location'].endswith('sso_error=invalid_response')
        assert (await client.get('/api/auth/saml/acs')).status_code == 401
        client.cookies.set(
            'openhands_session', existing.token, domain='native.example.com', path='/'
        )
        start = await client.post(
            '/api/auth/saml/start',
            json={},
            headers={
                'Origin': 'https://evil.test',
                'X-CSRF-Token': csrf_for_token(existing.token),
            },
        )
        assert start.status_code == 403


@pytest.mark.asyncio
async def test_old_idp_auth_cannot_refresh_recent_authority_and_reauth_caps_session(
    saml_runtime: SamlRuntime,
    saml_settings: SamlSettings,
    signing_material: SigningMaterial,
) -> None:
    _, protocol, service, existing = saml_runtime

    async def finish(
        *,
        link: bool = False,
        token: str | None = None,
        reauthenticate: bool = False,
        old: bool = False,
    ) -> NativeLogin:
        location, browser = await protocol.start(
            link=link, session_token=token, reauthenticate=reauthenticate
        )
        query = parse_qs(urlsplit(location).query)
        relay = query['RelayState'][0]
        transaction = await transaction_for(protocol, relay)

        def authn(root: XmlElement) -> None:
            _change(
                'saml:Assertion/saml:AuthnStatement', 'SessionNotOnOrAfter', _time(120)
            )(root)
            if old:
                _change(
                    'saml:Assertion/saml:AuthnStatement',
                    'AuthnInstant',
                    '2001-01-01T00:00:00Z',
                )(root)

        if reauthenticate or link:
            from onelogin.saml2.utils import OneLogin_Saml2_Utils

            xml = OneLogin_Saml2_Utils.decode_base64_and_inflate(
                query['SAMLRequest'][0]
            )
            assert b'ForceAuthn="true"' in xml
        await protocol.accept_response(
            relay,
            signed_response(
                saml_settings,
                signing_material,
                transaction.request_id,
                email=existing.principal.email,
                before_sign=authn,
            ),
        )
        return await protocol.complete(browser, token)

    linked = await finish(link=True, token=existing.token)
    older = await finish(token=linked.token, old=True)
    assert present(older.principal.auth_time).year == 2001
    with pytest.raises(NativeAuthError) as denied:
        await service.issue_password_reset(
            older.principal.account_id, older.principal.account_id, older.token
        )
    assert denied.value.status_code == 401
    with pytest.raises(NativeAuthError):
        await finish(token=older.token, old=True, reauthenticate=True)
    recent = await finish(token=older.token, reauthenticate=True)
    assert present(recent.principal.auth_time) > datetime.now(UTC) - timedelta(
        seconds=10
    )
    assert await service.issue_password_reset(
        recent.principal.account_id, recent.principal.account_id, recent.token
    )
    async with protocol.sessions() as session:
        stored = await session.get(BrowserSession, recent.principal.session_id)
        assert present(stored).absolute_expires_at <= datetime.now(UTC) + timedelta(
            seconds=125
        )
        assert present(stored).idle_expires_at <= present(stored).absolute_expires_at


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('case', 'expected'),
    [('email', 'account_link_required'), ('invitation', 'invitation_required')],
)
async def test_completion_returns_actionable_safe_code(
    saml_runtime: SamlRuntime,
    saml_settings: SamlSettings,
    signing_material: SigningMaterial,
    case: str,
    expected: str,
) -> None:
    app, protocol, _, existing = saml_runtime
    if case == 'invitation':
        protocol.settings = replace(saml_settings, allow_jit=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as browser:
        relay = await begin_browser(browser)
        transaction = await transaction_for(protocol, relay)
        await protocol.accept_response(
            relay,
            signed_response(
                present(protocol.settings),
                signing_material,
                transaction.request_id,
                email=existing.principal.email
                if case == 'email'
                else 'invited@example.test',
            ),
        )
        result = await complete_browser(browser)
        assert result.status_code in (403, 409)
        assert result.json()['code'] == expected
        assert 'example' not in result.text
        assert 'SAMLResponse' not in result.text


@pytest.mark.asyncio
async def test_verification_capacity_fails_fast_without_wait_queue(
    saml_runtime: SamlRuntime,
) -> None:
    _, protocol, _, _ = saml_runtime
    for _ in range(4):
        await protocol.verification_slots.acquire()
    try:
        with pytest.raises(NativeAuthError) as busy:
            await protocol.accept_response('some-relay', 'some-response')
        assert busy.value.status_code == 429
    finally:
        for _ in range(4):
            protocol.verification_slots.release()


@pytest.mark.asyncio
async def test_long_assertion_replay_retention_and_short_transaction(
    saml_runtime: SamlRuntime,
    saml_settings: SamlSettings,
    signing_material: SigningMaterial,
) -> None:
    _, protocol, _, _ = saml_runtime
    location, _ = await protocol.start()
    relay = parse_qs(urlsplit(location).query)['RelayState'][0]
    transaction = await transaction_for(protocol, relay)

    def lifetimes(root: XmlElement) -> None:
        _change('saml:Assertion/saml:Conditions', 'NotOnOrAfter', _time(3600))(root)
        _change(
            'saml:Assertion/saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData',
            'NotOnOrAfter',
            _time(7200),
        )(root)

    await protocol.accept_response(
        relay,
        signed_response(
            saml_settings,
            signing_material,
            transaction.request_id,
            before_sign=lifetimes,
        ),
    )
    async with protocol.sessions() as session:
        stored = await session.get(SamlTransaction, transaction.id)
        assert present(stored).expires_at <= datetime.now(UTC) + timedelta(seconds=600)
        retained = await session.scalar(select(func.min(SamlReplay.expires_at)))
        assert present(retained) > datetime.now(UTC) + timedelta(seconds=7255)
