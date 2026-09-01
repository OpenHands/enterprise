"""Unit and integration tests for organization provider-connections router."""

import uuid
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from storage.org import Org
from storage.role import Role
from storage.user import User

from openhands.app_server.settings.provider_connections import (
    ProviderConnection,
    ProviderConnectionLimitExceededError,
    ProviderConnections,
)

# Mock the database module before importing the router — matches the
# test_org_profiles.py pattern so module-level imports don't touch a real engine.
with patch('storage.database.a_session_maker'):
    from server.routes.org_profiles import (
        SaveProfileRequest,
        save_profile,
    )
    from server.routes.org_provider_connections import (
        ProviderConnectionCreateRequest,
        ProviderConnectionListResponse,
        ProviderConnectionResponse,
        ProviderConnectionUpdateRequest,
        _load_connections,
        create_provider_connection,
        delete_provider_connection,
        list_provider_connections,
        update_provider_connection,
    )

from openhands.app_server.settings.llm_profiles import StrictLLM

# ── Model-level tests (no DB) ────────────────────────────────────────────────


class TestProviderConnectionsModel:
    def test_create_and_summaries_hide_secret(self):
        pc = ProviderConnections()
        pc.create(
            ProviderConnection(id='a1', display_name='Shared', api_key='sk-secret')
        )
        summaries = pc.summaries()
        assert len(summaries) == 1
        s = summaries[0]
        assert s['id'] == 'a1'
        assert s['api_key_set'] is True
        # summaries never carry the raw key
        assert 'api_key' not in s

    def test_persisted_dump_exposes_key_only_with_flag(self):
        pc = ProviderConnections()
        pc.create(ProviderConnection(id='a1', display_name='S', api_key='sk-1'))
        persisted = pc.model_dump(mode='json', context={'expose_secrets': True})
        assert persisted['connections']['a1']['api_key'] == 'sk-1'
        safe = pc.model_dump(mode='json')
        assert safe['connections']['a1']['api_key'] is None

    def test_round_trip_through_validate(self):
        pc = ProviderConnections()
        pc.create(
            ProviderConnection(
                id='a1', display_name='S', api_key='sk-1', base_url='https://x'
            )
        )
        blob = pc.model_dump(mode='json', context={'expose_secrets': True})
        restored = ProviderConnections.model_validate(blob)
        conn = restored.get('a1')
        assert conn is not None
        assert conn.api_key_value() == 'sk-1'
        assert conn.base_url == 'https://x'

    def test_create_rejects_duplicate_id(self):
        pc = ProviderConnections()
        pc.create(ProviderConnection(id='a1', display_name='S', api_key='k'))
        with pytest.raises(ValueError):
            pc.create(ProviderConnection(id='a1', display_name='S2', api_key='k2'))

    def test_create_rejects_invalid_id(self):
        pc = ProviderConnections()
        with pytest.raises(ValueError):
            pc.create(ProviderConnection(id='has/slash', display_name='S', api_key='k'))

    def test_create_enforces_limit(self, monkeypatch):
        # The limit is read from the env at call time, not frozen at import.
        monkeypatch.setenv('MAX_PROVIDER_CONNECTIONS_PER_ORG', '2')
        pc = ProviderConnections()
        for i in range(2):
            pc.create(ProviderConnection(id=f'c{i}', display_name='S', api_key='k'))
        with pytest.raises(ProviderConnectionLimitExceededError) as exc:
            pc.create(ProviderConnection(id='overflow', display_name='S', api_key='k'))
        assert exc.value.limit == 2

    def test_create_enforces_default_limit_without_env(self, monkeypatch):
        monkeypatch.delenv('MAX_PROVIDER_CONNECTIONS_PER_ORG', raising=False)
        pc = ProviderConnections()
        for i in range(64):
            pc.create(ProviderConnection(id=f'c{i}', display_name='S', api_key='k'))
        with pytest.raises(ProviderConnectionLimitExceededError) as exc:
            pc.create(ProviderConnection(id='overflow', display_name='S', api_key='k'))
        assert exc.value.limit == 64

    def test_invalid_limit_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv('MAX_PROVIDER_CONNECTIONS_PER_ORG', 'not-an-int')
        pc = ProviderConnections()
        for i in range(64):
            pc.create(ProviderConnection(id=f'c{i}', display_name='S', api_key='k'))
        with pytest.raises(ProviderConnectionLimitExceededError):
            pc.create(ProviderConnection(id='overflow', display_name='S', api_key='k'))

    def test_delete(self):
        pc = ProviderConnections()
        pc.create(ProviderConnection(id='a1', display_name='S', api_key='k'))
        assert pc.delete('a1') is True
        assert pc.delete('a1') is False

    def test_skip_invalid_connection_on_load(self):
        # One good, one structurally invalid (missing display_name) entry.
        blob = {
            'connections': {
                'good': {'id': 'good', 'display_name': 'S', 'api_key': 'k'},
                'bad': {'id': 'bad'},
            }
        }
        pc = ProviderConnections.model_validate(blob)
        assert pc.has('good')
        assert not pc.has('bad')


class TestUpdateRequestValidation:
    def test_rejects_null_api_key(self):
        with pytest.raises(ValueError):
            ProviderConnectionUpdateRequest(api_key=None)

    def test_rejects_null_display_name(self):
        with pytest.raises(ValueError):
            ProviderConnectionUpdateRequest(display_name=None)

    def test_allows_null_base_url(self):
        req = ProviderConnectionUpdateRequest(base_url=None)
        assert 'base_url' in req.model_fields_set

    def test_rejects_unknown_field(self):
        with pytest.raises(ValueError):
            ProviderConnectionCreateRequest(
                display_name='S', api_key='k', custom_header='x'
            )


# ── Integration tests against a real Org row ─────────────────────────────────

ORG_ID = uuid.UUID('6694c7b6-f959-4b81-92e9-b09c206f5081')
ADMIN_USER_ID = uuid.UUID('6694c7b6-f959-4b81-92e9-b09c206f5082')


@pytest.fixture
def seeded_org(session_maker):
    with session_maker() as session:
        session.add(Role(id=10, name='member', rank=3))
        session.add(
            Org(
                id=ORG_ID,
                name='pc-test-org',
                org_version=1,
                enable_proactive_conversation_starters=True,
            )
        )
        session.add(
            User(
                id=ADMIN_USER_ID,
                current_org_id=ORG_ID,
                user_consents_to_analytics=True,
            )
        )
        session.commit()
    return {'org_id': ORG_ID, 'admin_user_id': ADMIN_USER_ID}


@pytest.fixture
def patch_route_db(async_session_maker, seeded_org):
    """Point both routers' db session + OrgService.get_org_by_id at the SQLite.

    fixture, so direct handler calls hit the real schema.
    """
    org_id = seeded_org['org_id']

    async def _fake_get_org(org_id, user_id):  # noqa: ARG001
        async with async_session_maker() as session:
            result = await session.execute(select(Org).where(Org.id == org_id))
            return result.scalars().first()

    with (
        patch(
            'server.routes.org_provider_connections.a_session_maker',
            async_session_maker,
        ),
        patch(
            'server.routes.org_provider_connections.OrgService.get_org_by_id',
            side_effect=_fake_get_org,
        ),
        patch(
            'server.routes.org_profiles.a_session_maker',
            async_session_maker,
        ),
        patch(
            'server.routes.org_profiles.OrgService.get_org_by_id',
            side_effect=_fake_get_org,
        ),
    ):
        yield org_id


async def _read_org(async_session_maker, org_id):
    async with async_session_maker() as session:
        result = await session.execute(select(Org).where(Org.id == org_id))
        return result.scalars().first()


class TestProviderConnectionLifecycle:
    @pytest.mark.asyncio
    async def test_create_then_list_hides_secret_and_persists_encrypted(
        self, async_session_maker, patch_route_db
    ):
        org_id = patch_route_db
        created = await create_provider_connection(
            org_id=org_id,
            request=ProviderConnectionCreateRequest(
                display_name='Shared OpenAI', api_key='sk-secret', base_url=None
            ),
            user_id=str(ADMIN_USER_ID),
        )
        assert isinstance(created, ProviderConnectionResponse)
        assert created.api_key_set is True

        listing = await list_provider_connections(
            org_id=org_id, user_id=str(ADMIN_USER_ID)
        )
        assert isinstance(listing, ProviderConnectionListResponse)
        assert len(listing.connections) == 1
        assert listing.connections[0].display_name == 'Shared OpenAI'
        # Response never exposes the raw key
        assert not hasattr(listing.connections[0], 'api_key')

        # The stored blob decrypts back to the real key (column-level cipher).
        org = await _read_org(async_session_maker, org_id)
        conns = _load_connections(org)
        assert conns.get(created.id).api_key_value() == 'sk-secret'

    @pytest.mark.asyncio
    async def test_update_rotates_key_and_clears_base_url(
        self, async_session_maker, patch_route_db
    ):
        org_id = patch_route_db
        created = await create_provider_connection(
            org_id=org_id,
            request=ProviderConnectionCreateRequest(
                display_name='S', api_key='sk-old', base_url='https://old'
            ),
            user_id=str(ADMIN_USER_ID),
        )
        await update_provider_connection(
            org_id=org_id,
            connection_id=created.id,
            request=ProviderConnectionUpdateRequest(api_key='sk-new', base_url=None),
            user_id=str(ADMIN_USER_ID),
        )
        org = await _read_org(async_session_maker, org_id)
        conn = _load_connections(org).get(created.id)
        assert conn.api_key_value() == 'sk-new'
        assert conn.base_url is None

    @pytest.mark.asyncio
    async def test_update_missing_returns_404(self, patch_route_db):
        org_id = patch_route_db
        with pytest.raises(HTTPException) as exc:
            await update_provider_connection(
                org_id=org_id,
                connection_id='deadbeef',
                request=ProviderConnectionUpdateRequest(display_name='X'),
                user_id=str(ADMIN_USER_ID),
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_then_repeat_returns_404(
        self, async_session_maker, patch_route_db
    ):
        org_id = patch_route_db
        created = await create_provider_connection(
            org_id=org_id,
            request=ProviderConnectionCreateRequest(display_name='S', api_key='k'),
            user_id=str(ADMIN_USER_ID),
        )
        await delete_provider_connection(
            org_id=org_id, connection_id=created.id, user_id=str(ADMIN_USER_ID)
        )
        with pytest.raises(HTTPException) as exc:
            await delete_provider_connection(
                org_id=org_id,
                connection_id=created.id,
                user_id=str(ADMIN_USER_ID),
            )
        assert exc.value.status_code == 404


class TestDeleteReferentialIntegrity:
    @pytest.mark.asyncio
    async def test_delete_blocked_while_profile_links_it(self, patch_route_db):
        org_id = patch_route_db
        created = await create_provider_connection(
            org_id=org_id,
            request=ProviderConnectionCreateRequest(display_name='S', api_key='k'),
            user_id=str(ADMIN_USER_ID),
        )
        # Save a profile that links to the connection.
        await save_profile(
            org_id=org_id,
            name='linked',
            request=SaveProfileRequest(
                llm=StrictLLM(
                    model='anthropic/claude-3-5-sonnet',
                    provider_connection_id=created.id,
                )
            ),
            user_id=str(ADMIN_USER_ID),
        )
        with pytest.raises(HTTPException) as exc:
            await delete_provider_connection(
                org_id=org_id,
                connection_id=created.id,
                user_id=str(ADMIN_USER_ID),
            )
        assert exc.value.status_code == 409
        assert 'linked' in exc.value.detail


class TestResolutionAtActivation:
    @pytest.mark.asyncio
    async def test_activate_linked_profile_resolves_key(
        self, async_session_maker, patch_route_db
    ):
        from server.routes.org_profiles import activate_profile

        org_id = patch_route_db
        created = await create_provider_connection(
            org_id=org_id,
            request=ProviderConnectionCreateRequest(
                display_name='S', api_key='sk-shared', base_url='https://prov'
            ),
            user_id=str(ADMIN_USER_ID),
        )
        # Seed a member row for the activation write.
        from storage.org_member import OrgMember

        async with async_session_maker() as session:
            session.add(
                OrgMember(
                    org_id=org_id,
                    user_id=ADMIN_USER_ID,
                    role_id=10,
                    llm_api_key='initial-key',
                    agent_settings_diff={},
                    conversation_settings_diff={},
                    status='active',
                )
            )
            await session.commit()

        await save_profile(
            org_id=org_id,
            name='linked',
            request=SaveProfileRequest(
                llm=StrictLLM(
                    model='anthropic/claude-3-5-sonnet',
                    provider_connection_id=created.id,
                )
            ),
            user_id=str(ADMIN_USER_ID),
        )
        resp = await activate_profile(
            org_id=org_id, name='linked', user_id=str(ADMIN_USER_ID)
        )
        # Resolved base_url from the connection shows up in the (secret-free)
        # activation response.
        assert resp.llm['base_url'] == 'https://prov'

    @pytest.mark.asyncio
    async def test_activate_dangling_reference_returns_422(self, patch_route_db):
        from server.routes.org_profiles import activate_profile

        org_id = patch_route_db
        await save_profile(
            org_id=org_id,
            name='dangling',
            request=SaveProfileRequest(
                llm=StrictLLM(
                    model='anthropic/claude-3-5-sonnet',
                    provider_connection_id='does-not-exist',
                )
            ),
            user_id=str(ADMIN_USER_ID),
        )
        with pytest.raises(HTTPException) as exc:
            await activate_profile(
                org_id=org_id, name='dangling', user_id=str(ADMIN_USER_ID)
            )
        assert exc.value.status_code == 422
