"""Unit tests for OrgMcpServersStore (organization-scoped MCP server listings)."""

from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from openhands.app_server.settings.agent_profiles import AgentProfiles
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles import (
    OpenHandsAgentProfile,
    save_profile_preserving_identity,
)
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE
from storage.org import Org
from storage.org_mcp_servers import (
    OrgMcpServer,
    load_org_mcp_servers,
    redacted_org_mcp_server,
)
from storage.org_mcp_servers_store import (
    OrgMcpServerAlreadyExistsError,
    OrgMcpServerInUseError,
    OrgMcpServerNotFoundError,
    OrgMcpServersOrgMissingError,
    OrgMcpServersStore,
)

ORG_ID = UUID('c4444444-4444-4444-4444-444444444444')
DATADOG_URL = 'https://dd.example/mcp'
BIFROST_URL = 'https://bifrost.example/mcp'


def _bearer(url: str, token: str) -> MCPServer:
    return MCPServer(
        url=url, transport='http', auth={'strategy': 'bearer', 'value': token}
    )


def _org_managed(token: str = 'dd-service-key') -> OrgMcpServer:
    return OrgMcpServer(managed_by='org', server=_bearer(DATADOG_URL, token))


def _user_managed() -> OrgMcpServer:
    return OrgMcpServer(
        managed_by='user', server=_bearer(BIFROST_URL, '${BIFROST_TOKEN}')
    )


@pytest.fixture
def store(async_session_maker, create_org):
    create_org(id=ORG_ID)
    with patch('storage.org_mcp_servers_store.a_session_maker', async_session_maker):
        yield OrgMcpServersStore(ORG_ID)


async def _read_org(async_session_maker, org_id=ORG_ID) -> Org:
    async with async_session_maker() as session:
        result = await session.execute(select(Org).where(Org.id == org_id))
        return result.scalars().first()


async def _publish_org_profile(async_session_maker, mcp_server_refs):
    profiles = AgentProfiles()
    save_profile_preserving_identity(
        profiles,
        OpenHandsAgentProfile(
            name='ops', llm_profile_ref='Default', mcp_server_refs=mcp_server_refs
        ),
    )
    async with async_session_maker() as session:
        org = (
            (await session.execute(select(Org).where(Org.id == ORG_ID)))
            .scalars()
            .first()
        )
        org.agent_profiles = profiles.model_dump(mode='json')
        await session.commit()


class TestOrgMcpServersStoreCRUD:
    @pytest.mark.asyncio
    async def test_create_list_get(self, store):
        await store.create('bifrost', _user_managed())
        await store.create('datadog', _org_managed())

        listings = await store.list()

        assert set(listings) == {'bifrost', 'datadog'}
        assert listings['bifrost'].managed_by == 'user'
        assert listings['datadog'].managed_by == 'org'
        # The runtime view keeps the credential: it is what launches use.
        assert listings['datadog'].server.auth is not None
        assert (
            listings['datadog'].server.auth.value.get_secret_value() == 'dd-service-key'
        )
        assert (await store.get('bifrost')).server.url == BIFROST_URL
        assert await store.get('missing') is None

    @pytest.mark.asyncio
    async def test_credential_is_encrypted_at_rest_and_masked_for_reads(
        self, store, async_session_maker
    ):
        await store.create('datadog', _org_managed('dd-service-key'))

        async with async_session_maker() as session:
            raw = (
                await session.execute(
                    text('SELECT mcp_servers FROM org WHERE id = :id'), {'id': ORG_ID}
                )
            ).scalar_one()
        assert raw and 'dd-service-key' not in raw

        org = await _read_org(async_session_maker)
        loaded = load_org_mcp_servers(org)
        assert (
            loaded['datadog'].server.auth.value.get_secret_value() == 'dd-service-key'
        )
        redacted = redacted_org_mcp_server('datadog', loaded['datadog'])
        assert redacted['url'] == DATADOG_URL
        assert redacted['auth']['value'] == REDACTED_SECRET_VALUE

    @pytest.mark.asyncio
    async def test_create_duplicate_raises(self, store):
        await store.create('datadog', _org_managed())

        with pytest.raises(OrgMcpServerAlreadyExistsError):
            await store.create('datadog', _org_managed('other'))

    @pytest.mark.asyncio
    async def test_update_replaces_config_and_management(self, store):
        await store.create('datadog', _org_managed())

        await store.update(
            'datadog',
            OrgMcpServer(
                managed_by='user', server=_bearer(DATADOG_URL, '${DATADOG_KEY}')
            ),
        )

        updated = await store.get('datadog')
        assert updated.managed_by == 'user'
        assert updated.server.auth.value.get_secret_value() == '${DATADOG_KEY}'

    @pytest.mark.asyncio
    async def test_update_unknown_raises(self, store):
        with pytest.raises(OrgMcpServerNotFoundError):
            await store.update('ghost', _org_managed())

    @pytest.mark.asyncio
    async def test_delete(self, store):
        await store.create('datadog', _org_managed())
        await store.create('bifrost', _user_managed())

        await store.delete('datadog')

        assert set(await store.list()) == {'bifrost'}
        with pytest.raises(OrgMcpServerNotFoundError):
            await store.delete('datadog')

    @pytest.mark.asyncio
    async def test_missing_org_raises(self, async_session_maker):
        with patch(
            'storage.org_mcp_servers_store.a_session_maker', async_session_maker
        ):
            with pytest.raises(OrgMcpServersOrgMissingError):
                await OrgMcpServersStore(uuid4()).list()
            with pytest.raises(OrgMcpServersOrgMissingError):
                await OrgMcpServersStore(uuid4()).create('x', _org_managed())


class TestOrgMcpServersReferentialGuard:
    """An org Agent Profile that names a listing keeps it deletable only when
    the profile no longer references it, so launches never hit a dangling ref."""

    @pytest.mark.asyncio
    async def test_delete_referenced_listing_raises(self, store, async_session_maker):
        await store.create('datadog', _org_managed())
        await _publish_org_profile(async_session_maker, ['datadog'])

        with pytest.raises(OrgMcpServerInUseError) as exc:
            await store.delete('datadog')

        assert exc.value.profile_names == ['ops']
        assert set(await store.list()) == {'datadog'}

    @pytest.mark.asyncio
    async def test_handing_referenced_listing_to_users_raises(
        self, store, async_session_maker
    ):
        await store.create('datadog', _org_managed())
        await _publish_org_profile(async_session_maker, ['datadog'])

        with pytest.raises(OrgMcpServerInUseError):
            await store.update(
                'datadog',
                OrgMcpServer(
                    managed_by='user', server=_bearer(DATADOG_URL, '${DATADOG_KEY}')
                ),
            )

        assert (await store.get('datadog')).managed_by == 'org'

    @pytest.mark.asyncio
    async def test_referenced_listing_can_still_change_its_config(
        self, store, async_session_maker
    ):
        await store.create('datadog', _org_managed())
        await _publish_org_profile(async_session_maker, ['datadog'])

        await store.update('datadog', _org_managed('rotated-key'))

        assert (await store.get('datadog')).server.auth.value.get_secret_value() == (
            'rotated-key'
        )

    @pytest.mark.asyncio
    async def test_unreferenced_listing_is_deletable(self, store, async_session_maker):
        await store.create('datadog', _org_managed())
        await store.create('bifrost', _user_managed())
        await _publish_org_profile(async_session_maker, ['datadog'])

        await store.delete('bifrost')

        assert set(await store.list()) == {'datadog'}
