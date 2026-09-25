"""Unit tests for the OAuth v2 stores (Phase 1, OHE-3294).

Covers:
- ``OAuthProviderStore`` CRUD + ``get_idp_providers`` / ``get_git_providers``.
- ``OAuthProviderUserStore`` lookup + link/unlink.
- ``OAuthTokenStore``:
  - drift math (``_is_expired`` with ``None`` == never expires),
  - fast path (valid token, no refresh, no lock),
  - slow-path refresh (callback invoked + persisted),
  - double-check (refreshed by another worker while waiting → no double call),
  - lock timeout → ``TokenRefreshError``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from storage.oauth_provider import OAuthProvider
from storage.oauth_provider_store import OAuthProviderStore
from storage.oauth_provider_user_store import OAuthProviderUserStore
from storage.oauth_token import OAuthToken
from storage.oauth_token_store import (
    OAuthTokenStore,
    _is_expired,
    unwrap_token,
    wrap_token,
)


def _make_jwt_service() -> JwtService:
    key = EncryptionKey(kid='test', key=SecretStr('test_secret'), active=True)
    return JwtService(keys=[key])


# ── fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def jwt_svc():
    return _make_jwt_service()


@pytest.fixture
def patched_session(async_session_maker, jwt_svc):
    """Patch the module-level ``a_session_maker`` AND encryption lookups."""
    patches = [
        patch('storage.oauth_provider_store.a_session_maker', async_session_maker),
        patch('storage.oauth_provider_user_store.a_session_maker', async_session_maker),
        patch('storage.oauth_token_store.a_session_maker', async_session_maker),
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
    ]
    for p in patches:
        p.start()
    yield async_session_maker
    for p in patches:
        p.stop()


@pytest.fixture
def make_provider(async_session_maker, jwt_svc):
    """Insert an ``OAuthProvider`` row directly and return it."""

    async def _make(
        *,
        category: ProviderType = ProviderType.GITHUB,
        is_idp: bool = False,
        client_secret: str | None = 'secret',
    ) -> OAuthProvider:
        provider = OAuthProvider(
            provider_category=category.value,
            display_name=category.value.title(),
            is_idp=is_idp,
            client_id='cid-' + category.value,
            client_secret=wrap_token(client_secret) if client_secret else None,
            authorization_url='https://example.com/auth',
            token_url='https://example.com/token',
            userinfo_url='https://example.com/user',
            scopes=['repo'],
            permitted_drift_seconds=60,
        )
        async with async_session_maker() as session:
            session.add(provider)
            await session.commit()
            await session.refresh(provider)
        return provider

    return _make


# ── OAuthProviderStore ──────────────────────────────────────────────────


class TestOAuthProviderStore:
    @pytest.mark.asyncio
    async def test_get_by_id_found(self, patched_session, make_provider):
        provider = await make_provider()
        fetched = await OAuthProviderStore().get_by_id(provider.id)
        assert fetched is not None
        assert fetched.id == provider.id
        assert fetched.provider_category == ProviderType.GITHUB.value

    @pytest.mark.asyncio
    async def test_get_by_id_missing(self, patched_session):
        assert await OAuthProviderStore().get_by_id(99999) is None

    @pytest.mark.asyncio
    async def test_idp_and_git_split(self, patched_session, make_provider):
        await make_provider(category=ProviderType.GITHUB, is_idp=False)
        await make_provider(category=ProviderType.ENTERPRISE_SSO, is_idp=True)
        await make_provider(category=ProviderType.GITLAB, is_idp=False)

        idps = await OAuthProviderStore().get_idp_providers()
        gits = await OAuthProviderStore().get_git_providers()
        assert [p.provider_category for p in idps] == ['enterprise_sso']
        assert sorted(p.provider_category for p in gits) == [
            'github',
            'gitlab',
        ]

    @pytest.mark.asyncio
    async def test_get_by_category(self, patched_session, make_provider):
        await make_provider(category=ProviderType.GITHUB)
        rows = await OAuthProviderStore().get_by_category(ProviderType.GITHUB)
        assert len(rows) == 1
        assert rows[0].provider_category == 'github'

    @pytest.mark.asyncio
    async def test_upsert_insert_then_update(self, patched_session):
        provider = OAuthProvider(
            provider_category=ProviderType.GITHUB.value,
            display_name='GitHub',
            is_idp=False,
            client_id='cid',
            permitted_drift_seconds=60,
        )
        inserted = await OAuthProviderStore().upsert(provider)
        assert inserted.id is not None
        inserted.display_name = 'GitHub Inc'
        updated = await OAuthProviderStore().upsert(inserted)
        assert updated.id == inserted.id
        assert updated.display_name == 'GitHub Inc'

    @pytest.mark.asyncio
    async def test_delete(self, patched_session, make_provider):
        provider = await make_provider()
        await OAuthProviderStore().delete(provider.id)
        assert await OAuthProviderStore().get_by_id(provider.id) is None


# ── OAuthProviderUserStore ──────────────────────────────────────────────


class TestOAuthProviderUserStore:
    @pytest.mark.asyncio
    async def test_link_then_get(self, patched_session, make_provider, create_user):
        provider = await make_provider()
        user_id = create_user().id
        row = await OAuthProviderUserStore().link(
            oauth_provider_id=provider.id,
            user_id=user_id,
            external_subject_id='ext-123',
            external_email='a@b.com',
        )
        fetched = await OAuthProviderUserStore().get(provider.id, 'ext-123')
        assert fetched is not None
        assert fetched.user_id == user_id
        assert fetched.external_email == 'a@b.com'
        assert row.id == fetched.id

    @pytest.mark.asyncio
    async def test_link_repoints_existing(
        self, patched_session, make_provider, create_user
    ):
        provider = await make_provider()
        first = create_user().id
        second = create_user().id
        await OAuthProviderUserStore().link(provider.id, first, 'ext-1', 'a@b.com')
        await OAuthProviderUserStore().link(provider.id, second, 'ext-1', 'c@d.com')
        fetched = await OAuthProviderUserStore().get(provider.id, 'ext-1')
        assert fetched.user_id == second
        assert fetched.external_email == 'c@d.com'

    @pytest.mark.asyncio
    async def test_unlink(self, patched_session, make_provider, create_user):
        provider = await make_provider()
        user_id = create_user().id
        await OAuthProviderUserStore().link(provider.id, user_id, 'ext-1', 'a@b.com')
        await OAuthProviderUserStore().unlink(provider.id, 'ext-1')
        assert await OAuthProviderUserStore().get(provider.id, 'ext-1') is None

    @pytest.mark.asyncio
    async def test_get_by_user(self, patched_session, make_provider, create_user):
        provider = await make_provider()
        user_id = create_user().id
        await OAuthProviderUserStore().link(provider.id, user_id, 'ext-1', 'a@b.com')
        row = await OAuthProviderUserStore().get_by_user(user_id, provider.id)
        assert row is not None
        assert row.external_subject_id == 'ext-1'


# ── drift math ──────────────────────────────────────────────────────────


class TestIsExpired:
    def test_none_never_expires(self):
        assert _is_expired(None, timedelta(seconds=60)) is False

    @pytest.mark.asyncio
    async def test_future_not_expired(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        assert _is_expired(future, timedelta(seconds=60)) is False

    @pytest.mark.asyncio
    async def test_past_expired(self):
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        assert _is_expired(past, timedelta(seconds=60)) is True

    def test_within_drift_is_expired(self):
        # Expiry 30s in the future with 60s drift → expired.
        soon = datetime.now(timezone.utc) + timedelta(seconds=30)
        assert _is_expired(soon, timedelta(seconds=60)) is True

    def test_naive_datetime_treated_as_utc(self):
        naive_past = datetime.utcnow() - timedelta(seconds=10)
        assert _is_expired(naive_past, timedelta(seconds=60)) is True


# ── token wrap/unwrap ───────────────────────────────────────────────────


class TestTokenWrap:
    def test_roundtrip(self):
        assert unwrap_token(wrap_token('abc')) == 'abc'

    def test_unwrap_none(self):
        assert unwrap_token(None) is None


# ── OAuthTokenStore ─────────────────────────────────────────────────────


class TestOAuthTokenStore:
    @pytest.mark.asyncio
    async def test_store_then_get_valid(
        self, patched_session, make_provider, create_user
    ):
        provider = await make_provider()
        user = create_user()
        store = OAuthTokenStore(user_id=user.id, oauth_provider_id=provider.id)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        await store.store_tokens(
            access_token='at-1',
            refresh_token='rt-1',
            access_token_expires_at=future,
            refresh_token_expires_at=None,
        )
        # Fast path: token valid → no refresh callback called.
        calls = []

        async def refresh(rt, at_exp, rt_exp):
            calls.append(rt)
            return None

        token = await store.get_valid_access_token(
            permitted_drift_seconds=60, refresh=refresh
        )
        assert token == 'at-1'
        assert calls == []  # not invoked

    @pytest.mark.asyncio
    async def test_no_row_returns_none(
        self, patched_session, make_provider, create_user
    ):
        provider = await make_provider()
        store = OAuthTokenStore(user_id=create_user().id, oauth_provider_id=provider.id)
        assert await store.get_valid_access_token() is None

    @pytest.mark.asyncio
    async def test_expired_access_refreshed(
        self, patched_session, make_provider, create_user
    ):
        provider = await make_provider()
        user = create_user()
        store = OAuthTokenStore(user_id=user.id, oauth_provider_id=provider.id)
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        await store.store_tokens(
            access_token='old-at',
            refresh_token='rt-1',
            access_token_expires_at=past,
            refresh_token_expires_at=None,
        )
        new_exp = datetime.now(timezone.utc) + timedelta(hours=2)
        calls = []

        async def refresh(rt, at_exp, rt_exp):
            calls.append(rt)
            return {
                'access_token': 'new-at',
                'refresh_token': 'new-rt',
                'access_token_expires_at': new_exp,
                'refresh_token_expires_at': None,
            }

        token = await store.get_valid_access_token(
            permitted_drift_seconds=60, refresh=refresh
        )
        assert token == 'new-at'
        assert calls == ['rt-1']
        # Persisted
        row = await store.get_raw()
        from storage.oauth_token_store import unwrap_token as _unwrap

        assert _unwrap(row.access_token) == 'new-at'

    @pytest.mark.asyncio
    async def test_no_refresh_callback_returns_expired(
        self, patched_session, make_provider, create_user
    ):
        provider = await make_provider()
        user = create_user()
        store = OAuthTokenStore(user_id=user.id, oauth_provider_id=provider.id)
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        await store.store_tokens(
            access_token='old-at',
            refresh_token='rt-1',
            access_token_expires_at=past,
            refresh_token_expires_at=None,
        )
        token = await store.get_valid_access_token(refresh=None)
        assert token == 'old-at'

    @pytest.mark.asyncio
    async def test_null_expiry_never_expires(
        self, patched_session, make_provider, create_user
    ):
        provider = await make_provider()
        user = create_user()
        store = OAuthTokenStore(user_id=user.id, oauth_provider_id=provider.id)
        await store.store_tokens(
            access_token='at',
            refresh_token=None,
            access_token_expires_at=None,
            refresh_token_expires_at=None,
        )
        token = await store.get_valid_access_token()
        assert token == 'at'

    @pytest.mark.asyncio
    async def test_delete_tokens(self, patched_session, make_provider, create_user):
        provider = await make_provider()
        user = create_user()
        store = OAuthTokenStore(user_id=user.id, oauth_provider_id=provider.id)
        await store.store_tokens(
            access_token='at',
            refresh_token='rt',
            access_token_expires_at=None,
            refresh_token_expires_at=None,
        )
        await store.delete_tokens()
        assert await store.get_raw() is None


# ── lock double-check & timeout (require SELECT FOR UPDATE + lock_timeout) ──


class TestOAuthTokenStoreLocking:
    @pytest.mark.asyncio
    async def test_double_check_avoids_double_refresh(
        self, patched_session, make_provider, create_user
    ):
        """If the row is refreshed between the fast-path miss and acquiring the
        lock, the refresh callback must NOT be called a second time."""
        provider = await make_provider()
        user = create_user()
        store = OAuthTokenStore(user_id=user.id, oauth_provider_id=provider.id)

        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        await store.store_tokens(
            access_token='old-at',
            refresh_token='rt',
            access_token_expires_at=past,
            refresh_token_expires_at=None,
        )
        calls = []

        async def refresh(rt, at_exp, rt_exp):
            calls.append(rt)
            return None

        # Simulate another worker refreshing right after the fast-path miss:
        # update the row to a valid token before get_valid_access_token runs.
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        async with patched_session() as session:
            result = await session.execute(
                select(OAuthToken).where(
                    OAuthToken.user_id == user.id,
                    OAuthToken.oauth_provider_id == provider.id,
                )
            )
            row = result.scalars().one()
            row.access_token_expires_at = future
            await session.commit()

        token = await store.get_valid_access_token(
            permitted_drift_seconds=60, refresh=refresh
        )
        assert token is not None
        assert calls == []  # double-check short-circuited the refresh

    @pytest.mark.asyncio
    async def test_lock_timeout_raises_token_refresh_error(
        self, patched_session, make_provider, create_user
    ):
        """A lock timeout (simulated) surfaces as ``TokenRefreshError``."""
        from sqlalchemy.exc import OperationalError

        from server.auth.auth_error import TokenRefreshError

        provider = await make_provider()
        user = create_user()
        store = OAuthTokenStore(user_id=user.id, oauth_provider_id=provider.id)
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        await store.store_tokens(
            access_token='old-at',
            refresh_token='rt',
            access_token_expires_at=past,
            refresh_token_expires_at=None,
        )

        async def refresh(rt, at_exp, rt_exp):
            return {
                'access_token': 'new',
                'refresh_token': 'new',
                'access_token_expires_at': datetime.now(timezone.utc)
                + timedelta(hours=1),
                'refresh_token_expires_at': None,
            }

        # The fast path must succeed (read the expired token); only the slow
        # path's session entry raises to mimic ``lock_timeout`` aborting
        # ``SELECT FOR UPDATE``. Delegate the first invocation to the real
        # (already-patched) session maker; raise on the second.
        real_maker = patched_session
        call_count = {'n': 0}

        class _FakeSessionMaker:
            async def __aenter__(self_inner):
                call_count['n'] += 1
                if call_count['n'] == 1:
                    # Fast path: delegate to the real session maker.
                    self_inner._real = real_maker()
                    return await self_inner._real.__aenter__()

                raise OperationalError(
                    'SELECT FOR UPDATE', {}, Exception('lock timeout')
                )

            async def __aexit__(self_inner, *exc):
                real = getattr(self_inner, '_real', None)
                if real is not None:
                    return await real.__aexit__(*exc)
                return False

            def __call__(self_inner, **kwargs):
                return self_inner

        with patch('storage.oauth_token_store.a_session_maker', _FakeSessionMaker()):
            with pytest.raises(TokenRefreshError):
                await store.get_valid_access_token(
                    permitted_drift_seconds=60, refresh=refresh
                )
