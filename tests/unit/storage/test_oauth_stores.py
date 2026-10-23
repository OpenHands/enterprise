"""Unit tests for the OAuth v2 stores (Phase 1, OHE-3294; reshaped OHE-3608).

Covers:
- ``AuthProviderStore`` CRUD + ``get_idp_providers`` / ``get_git_providers`` /
  ``get_first_by_kind``.
- ``AuthProviderUserStore`` lookup + link/unlink.
- ``OAuthTokenStore``:
  - drift math (``_is_expired`` with ``None`` == never expires),
  - fast path (valid token, no refresh, no lock),
  - slow-path refresh (callback invoked + persisted),
  - double-check (refreshed by another worker while waiting → no double call),
  - lock timeout → ``TokenRefreshError``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import OAuthProviderDetails, PasswordProviderDetails
from storage.auth_provider_store import AuthProviderStore
from storage.auth_provider_user_store import AuthProviderUserStore
from storage.oauth_token import OAuthToken
from storage.oauth_token_store import (
    OAuthTokenStore,
    _is_expired,
    unwrap_token,
    wrap_token,
)

# Sentinel passed as ``make_provider(category=...)`` to build the
# integrated/password IDP row instead of an OAuth2 one.
INTEGRATED_IDP_KIND = PasswordProviderDetails.__name__


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
        patch('storage.auth_provider_store.a_session_maker', async_session_maker),
        patch('storage.auth_provider_user_store.a_session_maker', async_session_maker),
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
    """Insert an ``AuthProvider`` row directly and return it.

    ``category=INTEGRATED_IDP_KIND`` (or passing ``PasswordProviderDetails``
    directly) builds the integrated/password IDP row instead of an OAuth2
    one -- it has no brand/client/URLs, mirroring migration 186's seed.
    """

    async def _make(
        *,
        category: ProviderType | str = ProviderType.GITHUB,
        is_idp: bool = False,
        client_secret: str | None = 'secret',
        token_url: str | None = 'https://example.com/token',
    ) -> AuthProvider:
        if category == INTEGRATED_IDP_KIND:
            details = PasswordProviderDetails()
            title = 'Integrated IDP'
        else:
            category_value = (
                category.value if isinstance(category, ProviderType) else category
            )
            details = OAuthProviderDetails(
                provider_type=ProviderType(category_value),
                client_id='cid-' + category_value,
                client_secret=SecretStr(client_secret) if client_secret else None,
                authorization_url='https://example.com/auth' if token_url else None,
                token_url=token_url,
                userinfo_url='https://example.com/user' if token_url else None,
                scopes=['repo'],
                permitted_drift_seconds=60,
            )
            title = category_value.title()
        provider = AuthProvider(title=title, is_idp=is_idp, details=details)
        async with async_session_maker() as session:
            session.add(provider)
            await session.commit()
            await session.refresh(provider)
        return provider

    return _make


# ── AuthProviderStore ──────────────────────────────────────────────────


class TestAuthProviderStore:
    @pytest.mark.asyncio
    async def test_get_by_id_found(self, patched_session, make_provider):
        provider = await make_provider()
        fetched = await AuthProviderStore().get_by_id(provider.id)
        assert fetched is not None
        assert fetched.id == provider.id
        assert fetched.details.provider_type == ProviderType.GITHUB

    @pytest.mark.asyncio
    async def test_get_by_id_missing(self, patched_session):
        assert await AuthProviderStore().get_by_id(99999) is None

    @pytest.mark.asyncio
    async def test_idp_and_git_split(self, patched_session, make_provider):
        await make_provider(category=ProviderType.GITHUB, is_idp=False)
        await make_provider(category=ProviderType.ENTERPRISE_SSO, is_idp=True)
        await make_provider(category=ProviderType.GITLAB, is_idp=False)

        idps = await AuthProviderStore().get_idp_providers()
        gits = await AuthProviderStore().get_git_providers()
        assert [p.details.provider_type for p in idps] == [ProviderType.ENTERPRISE_SSO]
        assert sorted(p.details.provider_type.value for p in gits) == [
            'github',
            'gitlab',
        ]

    @pytest.mark.asyncio
    async def test_get_provider_by_type_found(self, patched_session, make_provider):
        """Used by ``server.routes.oauth_v2`` to find the git-provider row a
        ``legacy_keycloak_type`` login IDP's real, broker-fetched token
        should be stored against."""
        github = await make_provider(category=ProviderType.GITHUB, is_idp=False)
        await make_provider(category=ProviderType.GITLAB, is_idp=False)
        await make_provider(category=ProviderType.ENTERPRISE_SSO, is_idp=True)

        got = await AuthProviderStore().get_provider_by_type('github')
        assert got is not None
        assert got.id == github.id

    @pytest.mark.asyncio
    async def test_get_provider_by_type_ignores_is_idp(
        self, patched_session, make_provider
    ):
        """A git provider that doubles as a login IDP is still a match."""
        github_idp = await make_provider(category=ProviderType.GITHUB, is_idp=True)

        got = await AuthProviderStore().get_provider_by_type('github')
        assert got is not None
        assert got.id == github_idp.id

    @pytest.mark.asyncio
    async def test_get_provider_by_type_lowest_id_wins(
        self, patched_session, make_provider
    ):
        first = await make_provider(category=ProviderType.GITHUB, is_idp=False)
        await make_provider(category=ProviderType.GITHUB, is_idp=True)

        got = await AuthProviderStore().get_provider_by_type('github')
        assert got is not None
        assert got.id == first.id

    @pytest.mark.asyncio
    async def test_get_provider_by_type_none(self, patched_session, make_provider):
        await make_provider(category=ProviderType.GITLAB, is_idp=False)
        assert await AuthProviderStore().get_provider_by_type('github') is None

    @pytest.mark.asyncio
    async def test_get_idp_providers_lists_integrated_idp_alongside_real_idp(
        self, patched_session, make_provider
    ):
        """``get_idp_providers`` lists every ``is_idp`` row, including the
        integrated/password IDP, which (since migration 179) is a real row
        like any other — ``_v2_get_idp_access_token`` relies on this to
        resolve a session's provider by id for sessions authenticated
        against either kind."""
        real = await make_provider(category=ProviderType.ENTERPRISE_SSO, is_idp=True)
        integrated = await make_provider(category=INTEGRATED_IDP_KIND, is_idp=True)

        idps = await AuthProviderStore().get_idp_providers()

        assert {p.id for p in idps} == {real.id, integrated.id}

    @pytest.mark.asyncio
    async def test_get_first_by_kind(self, patched_session, make_provider):
        """``get_first_by_kind`` is ``server.routes.idp``'s protocol lookup
        for the integrated/password IDP row -- not a brand lookup (that was
        ``get_first_by_category``, deleted along with
        ``GET /oauth/{provider_type}-login`` in migration 186's rework).

        Only one row (created here) -- migration 186's
        ``uq_auth_providers_one_password_provider`` partial unique index
        makes a second ``PasswordProviderDetails`` row impossible to create,
        so there is no "picks the lowest id among duplicates" case to cover.
        """
        await make_provider(category=ProviderType.GITHUB, is_idp=False)
        only = await make_provider(category=INTEGRATED_IDP_KIND, is_idp=True)

        got = await AuthProviderStore().get_first_by_kind(
            PasswordProviderDetails.__name__
        )
        assert got is not None
        assert got.id == only.id
        assert isinstance(got.details, PasswordProviderDetails)

    @pytest.mark.asyncio
    async def test_get_first_by_kind_none(self, patched_session, make_provider):
        await make_provider(category=ProviderType.GITHUB)
        assert (
            await AuthProviderStore().get_first_by_kind(
                PasswordProviderDetails.__name__
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_get_first_idp_picks_most_recently_created(
        self, patched_session, make_provider
    ):
        """``get_first_idp`` (used by ``/oauth/idp-login``) picks whichever
        ``is_idp`` row was created most recently — not the oldest, and not
        special-cased by kind."""
        await make_provider(category=ProviderType.ENTERPRISE_SSO, is_idp=True)
        await make_provider(category=ProviderType.GITHUB, is_idp=False)
        last = await make_provider(category=ProviderType.ENTERPRISE_SSO, is_idp=True)

        got = await AuthProviderStore().get_first_idp()
        assert got is not None
        assert got.id == last.id
        assert got.is_idp is True

    @pytest.mark.asyncio
    async def test_get_first_idp_none(self, patched_session, make_provider):
        await make_provider(category=ProviderType.GITHUB, is_idp=False)
        assert await AuthProviderStore().get_first_idp() is None

    @pytest.mark.asyncio
    async def test_get_first_idp_integrated_idp_wins_when_created_after_real_idp(
        self, patched_session, make_provider
    ):
        """The integrated/password IDP is a real row like any other: when it
        was configured (seeded) after a real IDP, it is what
        ``/oauth/idp-login`` uses — not merely a fallback for when no real
        IDP is configured."""
        await make_provider(category=ProviderType.ENTERPRISE_SSO, is_idp=True)
        integrated = await make_provider(
            category=INTEGRATED_IDP_KIND, is_idp=True, token_url=None
        )

        got = await AuthProviderStore().get_first_idp()

        assert got is not None
        assert got.id == integrated.id

    @pytest.mark.asyncio
    async def test_get_first_idp_real_idp_wins_when_created_after_integrated_idp(
        self, patched_session, make_provider
    ):
        """The reverse of the above: a real IDP configured after the
        integrated one takes priority for ``/oauth/idp-login``."""
        await make_provider(category=INTEGRATED_IDP_KIND, is_idp=True, token_url=None)
        real = await make_provider(category=ProviderType.ENTERPRISE_SSO, is_idp=True)

        got = await AuthProviderStore().get_first_idp()

        assert got is not None
        assert got.id == real.id

    @pytest.mark.asyncio
    async def test_upsert_insert_then_update(self, patched_session):
        provider = AuthProvider(
            title='GitHub',
            is_idp=False,
            details=OAuthProviderDetails(
                provider_type=ProviderType.GITHUB,
                client_id='cid',
                permitted_drift_seconds=60,
            ),
        )
        inserted = await AuthProviderStore().upsert(provider)
        assert inserted.id is not None
        inserted.title = 'GitHub Inc'
        updated = await AuthProviderStore().upsert(inserted)
        assert updated.id == inserted.id
        assert updated.title == 'GitHub Inc'

    @pytest.mark.asyncio
    async def test_create(self, patched_session):
        provider = AuthProvider(
            title='GitLab',
            is_idp=False,
            details=OAuthProviderDetails(
                provider_type=ProviderType.GITLAB,
                client_id='cid',
                client_secret=SecretStr('secret'),
                permitted_drift_seconds=60,
            ),
        )
        created = await AuthProviderStore().create(provider)
        assert created.id is not None
        fetched = await AuthProviderStore().get_by_id(created.id)
        assert fetched is not None
        assert fetched.details.provider_type == ProviderType.GITLAB
        assert fetched.details.client_secret.get_secret_value() == 'secret'

    @pytest.mark.asyncio
    async def test_update_partial_fields_only(self, patched_session, make_provider):
        provider = await make_provider(client_secret='original')
        updated = await AuthProviderStore().update(provider.id, title='Renamed')
        assert updated is not None
        assert updated.title == 'Renamed'
        # Fields not passed to update() are left untouched.
        assert updated.details.client_id == provider.details.client_id
        assert updated.details.client_secret.get_secret_value() == 'original'

    @pytest.mark.asyncio
    async def test_update_can_clear_secret(self, patched_session, make_provider):
        """Replacing ``details`` as a whole blob is how a secret is cleared --
        there is no field-by-field merge (see ``UpdateAuthProviderRequest``)."""
        provider = await make_provider(client_secret='original')
        cleared_details = provider.details.model_copy(update={'client_secret': None})
        updated = await AuthProviderStore().update(provider.id, details=cleared_details)
        assert updated is not None
        assert updated.details.client_secret is None

    @pytest.mark.asyncio
    async def test_update_syncs_kind_from_details(self, patched_session, make_provider):
        """``kind`` can never drift from ``details`` -- reassigning ``details``
        (e.g. reconfiguring a row from OAuth2 to password) re-derives it.

        ``is_idp=True`` is passed alongside ``details`` here because
        migration 186's ``ck_auth_providers_password_provider_is_idp`` check
        constraint requires it for any ``PasswordProviderDetails`` row --
        the fixture's default ``is_idp=False`` would otherwise violate it.
        """
        provider = await make_provider(category=ProviderType.GITHUB)
        assert provider.kind == OAuthProviderDetails.__name__

        updated = await AuthProviderStore().update(
            provider.id, details=PasswordProviderDetails(), is_idp=True
        )
        assert updated is not None
        assert updated.kind == PasswordProviderDetails.__name__

    @pytest.mark.asyncio
    async def test_update_missing_returns_none(self, patched_session):
        assert await AuthProviderStore().update(99999, title='x') is None

    @pytest.mark.asyncio
    async def test_delete(self, patched_session, make_provider):
        provider = await make_provider()
        await AuthProviderStore().delete(provider.id)
        assert await AuthProviderStore().get_by_id(provider.id) is None


# ── AuthProviderUserStore ──────────────────────────────────────────────


class TestAuthProviderUserStore:
    @pytest.mark.asyncio
    async def test_link_then_get(self, patched_session, make_provider, create_user):
        provider = await make_provider()
        user_id = create_user().id
        row = await AuthProviderUserStore().link(
            oauth_provider_id=provider.id,
            user_id=user_id,
            external_subject_id='ext-123',
            external_email='a@b.com',
        )
        fetched = await AuthProviderUserStore().get(provider.id, 'ext-123')
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
        await AuthProviderUserStore().link(provider.id, first, 'ext-1', 'a@b.com')
        await AuthProviderUserStore().link(provider.id, second, 'ext-1', 'c@d.com')
        fetched = await AuthProviderUserStore().get(provider.id, 'ext-1')
        assert fetched.user_id == second
        assert fetched.external_email == 'c@d.com'

    @pytest.mark.asyncio
    async def test_unlink(self, patched_session, make_provider, create_user):
        provider = await make_provider()
        user_id = create_user().id
        await AuthProviderUserStore().link(provider.id, user_id, 'ext-1', 'a@b.com')
        await AuthProviderUserStore().unlink(provider.id, 'ext-1')
        assert await AuthProviderUserStore().get(provider.id, 'ext-1') is None

    @pytest.mark.asyncio
    async def test_get_by_user(self, patched_session, make_provider, create_user):
        provider = await make_provider()
        user_id = create_user().id
        await AuthProviderUserStore().link(provider.id, user_id, 'ext-1', 'a@b.com')
        row = await AuthProviderUserStore().get_by_user(user_id, provider.id)
        assert row is not None
        assert row.external_subject_id == 'ext-1'

    @pytest.mark.asyncio
    async def test_get_by_id_found(self, patched_session, make_provider, create_user):
        provider = await make_provider()
        user_id = create_user().id
        link = await AuthProviderUserStore().link(
            provider.id, user_id, 'ext-1', 'a@b.com'
        )
        fetched = await AuthProviderUserStore().get_by_id(link.id)
        assert fetched is not None
        assert fetched.user_id == user_id

    @pytest.mark.asyncio
    async def test_get_by_id_missing(self, patched_session):
        assert await AuthProviderUserStore().get_by_id(99999) is None

    @pytest.mark.asyncio
    async def test_list_by_provider_scopes_to_provider(
        self, patched_session, make_provider, create_user
    ):
        provider_a = await make_provider(category=ProviderType.GITHUB)
        provider_b = await make_provider(category=ProviderType.GITLAB)
        user = create_user(email='a@example.com')
        await AuthProviderUserStore().link(provider_a.id, user.id, 'ext-a', 'a@b.com')
        await AuthProviderUserStore().link(provider_b.id, user.id, 'ext-b', 'c@d.com')

        rows = await AuthProviderUserStore().list_by_provider(provider_a.id)
        assert [link.external_subject_id for link, _user in rows] == ['ext-a']
        assert await AuthProviderUserStore().count_by_provider(provider_a.id) == 1

    @pytest.mark.asyncio
    async def test_list_by_provider_joins_user_email(
        self, patched_session, make_provider, create_user
    ):
        provider = await make_provider()
        user = create_user(email='owner@example.com')
        await AuthProviderUserStore().link(
            provider.id, user.id, 'ext-1', 'ext@example.com'
        )

        rows = await AuthProviderUserStore().list_by_provider(provider.id)
        assert len(rows) == 1
        link, joined_user = rows[0]
        assert joined_user.email == 'owner@example.com'
        assert link.external_email == 'ext@example.com'

    @pytest.mark.asyncio
    async def test_list_by_provider_pagination(
        self, patched_session, make_provider, create_user
    ):
        provider = await make_provider()
        for i in range(3):
            await AuthProviderUserStore().link(
                provider.id, create_user().id, f'ext-{i}', f'{i}@example.com'
            )

        page1 = await AuthProviderUserStore().list_by_provider(
            provider.id, offset=0, limit=2
        )
        page2 = await AuthProviderUserStore().list_by_provider(
            provider.id, offset=2, limit=2
        )
        assert len(page1) == 2
        assert len(page2) == 1
        assert await AuthProviderUserStore().count_by_provider(provider.id) == 3

    @pytest.mark.asyncio
    async def test_list_by_provider_filters_on_user_email(
        self, patched_session, make_provider, create_user
    ):
        provider = await make_provider()
        matching = create_user(email='findme@example.com')
        create_user(email='other@example.com')
        await AuthProviderUserStore().link(
            provider.id, matching.id, 'ext-1', 'a@example.com'
        )
        await AuthProviderUserStore().link(
            provider.id, create_user().id, 'ext-2', 'b@example.com'
        )

        rows = await AuthProviderUserStore().list_by_provider(
            provider.id, filter_text='findme'
        )
        assert len(rows) == 1
        assert rows[0][1].email == 'findme@example.com'
        assert (
            await AuthProviderUserStore().count_by_provider(
                provider.id, filter_text='findme'
            )
            == 1
        )

    @pytest.mark.asyncio
    async def test_list_by_provider_filters_on_external_email_and_subject(
        self, patched_session, make_provider, create_user
    ):
        provider = await make_provider()
        await AuthProviderUserStore().link(
            provider.id, create_user().id, 'ext-subject-xyz', 'findme@git.example.com'
        )
        await AuthProviderUserStore().link(
            provider.id, create_user().id, 'ext-2', 'other@git.example.com'
        )

        by_email = await AuthProviderUserStore().list_by_provider(
            provider.id, filter_text='findme'
        )
        assert [link.external_subject_id for link, _ in by_email] == ['ext-subject-xyz']

        by_subject = await AuthProviderUserStore().list_by_provider(
            provider.id, filter_text='subject-xyz'
        )
        assert [link.external_subject_id for link, _ in by_subject] == [
            'ext-subject-xyz'
        ]

    @pytest.mark.asyncio
    async def test_update_user_sets_only_user_id(
        self, patched_session, make_provider, create_user
    ):
        provider = await make_provider()
        old_user = create_user()
        new_user = create_user()
        link = await AuthProviderUserStore().link(
            provider.id, old_user.id, 'ext-1', 'ext@example.com'
        )

        updated = await AuthProviderUserStore().update_user(link.id, new_user.id)
        assert updated is not None
        assert updated.user_id == new_user.id
        # external_email must be preserved -- unlike link(), which would
        # overwrite it with None when the argument is omitted.
        assert updated.external_email == 'ext@example.com'
        assert updated.external_subject_id == 'ext-1'

    @pytest.mark.asyncio
    async def test_update_user_missing_link_returns_none(
        self, patched_session, create_user
    ):
        assert (
            await AuthProviderUserStore().update_user(99999, create_user().id) is None
        )


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
        """A row lock held past ``lock_timeout`` surfaces as ``TokenRefreshError``."""
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
        refresh = AsyncMock()

        async with patched_session() as other_worker, other_worker.begin():
            await other_worker.execute(
                select(OAuthToken)
                .where(OAuthToken.oauth_provider_id == provider.id)
                .with_for_update()
            )
            with (
                patch('storage.oauth_token_store.LOCK_TIMEOUT_SECONDS', 1),
                pytest.raises(TokenRefreshError),
            ):
                await store.get_valid_access_token(
                    permitted_drift_seconds=60, refresh=refresh
                )

        refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_database_error_that_is_not_a_lock_timeout_propagates(
        self, patched_session, make_provider, create_user
    ):
        from server.auth.auth_error import TokenRefreshError

        provider = await make_provider()
        user = create_user()
        store = OAuthTokenStore(user_id=user.id, oauth_provider_id=provider.id)
        await store.store_tokens(
            access_token='old-at',
            refresh_token='rt',
            access_token_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
            refresh_token_expires_at=None,
        )

        # An invalid lock_timeout makes the slow path's SET LOCAL fail with
        # SQLSTATE 22023 instead of 55P03.
        with (
            patch('storage.oauth_token_store.LOCK_TIMEOUT_SECONDS', 'not-a-number'),
            pytest.raises(DBAPIError) as raised,
        ):
            await store.get_valid_access_token(
                permitted_drift_seconds=60, refresh=AsyncMock()
            )

        assert not isinstance(raised.value, TokenRefreshError)
