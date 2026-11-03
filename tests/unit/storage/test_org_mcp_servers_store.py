"""Unit tests for OrgMcpServersStore (organization-scoped MCP server listings)."""

from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from openhands.app_server.settings.agent_profiles import AgentProfiles
from openhands.sdk.mcp.config import MCPOAuthState, MCPServer
from openhands.sdk.profiles import (
    OpenHandsAgentProfile,
    save_profile_preserving_identity,
)
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE
from storage.agent_profile_resolution import launch_mcp_config
from storage.org import Org
from storage.org_mcp_servers import (
    OrgMcpServer,
    load_org_mcp_servers,
    org_launch_mcp_servers,
    redacted_org_mcp_server,
)
from storage.org_mcp_servers_store import (
    OrgMcpMemberMissingError,
    OrgMcpServerAlreadyExistsError,
    OrgMcpServerConnectionError,
    OrgMcpServerInUseError,
    OrgMcpServerNotFoundError,
    OrgMcpServersOrgMissingError,
    OrgMcpServersStore,
)
from storage.org_member import OrgMember
from storage.role import Role

ORG_ID = UUID('c4444444-4444-4444-4444-444444444444')
MEMBER_ID = UUID('c5555555-5555-5555-5555-555555555555')
DATADOG_URL = 'https://dd.example/mcp'
BIFROST_URL = 'https://bifrost.example/mcp'
JIRA_URL = 'https://mcp.atlassian.example/v1/mcp'


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


def _oauth(url: str = JIRA_URL, state: dict | None = None) -> MCPServer:
    auth: dict = {
        'strategy': 'oauth2',
        'authentication': {
            'type': 'oauth',
            'client_id': 'cid',
            'client_secret': 'client-secret',
        },
    }
    if state is not None:
        auth['state'] = state
    return MCPServer.model_validate({'url': url, 'transport': 'http', 'auth': auth})


def _state(access_token: str) -> MCPOAuthState:
    return MCPOAuthState.model_validate(
        {
            'tokens': {
                'access_token': access_token,
                'refresh_token': 'r-' + access_token,
            },
            'token_expires_at': 42.0,
        }
    )


ADMIN_STATE = {'tokens': {'access_token': 'admin-access'}}


@pytest.fixture
def store(async_session_maker, create_org):
    create_org(id=ORG_ID)
    with patch('storage.org_mcp_servers_store.a_session_maker', async_session_maker):
        yield OrgMcpServersStore(ORG_ID)


@pytest.fixture
def member(store, session_maker, create_user):
    """An ``org_member`` row for ``MEMBER_ID`` in ``ORG_ID``; returns its user id."""
    create_user(id=MEMBER_ID, current_org_id=ORG_ID)
    with session_maker() as session:
        role = Role(name=f'member-{uuid4().hex[:8]}', rank=3)
        session.add(role)
        session.flush()
        session.add(
            OrgMember(
                org_id=ORG_ID,
                user_id=MEMBER_ID,
                role_id=role.id,
                llm_api_key='k',
                status='active',
            )
        )
        session.commit()
    return MEMBER_ID


async def _read_member(async_session_maker) -> OrgMember:
    async with async_session_maker() as session:
        result = await session.execute(
            select(OrgMember).where(
                OrgMember.org_id == ORG_ID, OrgMember.user_id == MEMBER_ID
            )
        )
        return result.scalars().first()


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


class TestMemberOAuthState:
    """A user-managed OAuth listing holds the client configuration; the tokens
    each member obtains live on their own row and join the listing at launch."""

    @pytest.mark.asyncio
    async def test_set_list_and_clear(self, store, member, async_session_maker):
        await store.create('jira', OrgMcpServer(managed_by='user', server=_oauth()))

        await store.set_member_oauth_state(member, 'jira', _state('mine'))

        listings = await store.list()
        assert await store.member_connected_names(member, listings) == {'jira'}
        raw = (await _read_member(async_session_maker)).org_mcp_oauth_states
        assert raw == {
            'jira': {
                'server_url': JIRA_URL,
                'client_id': 'cid',
                'state': {
                    'tokens': {'access_token': 'mine', 'refresh_token': 'r-mine'},
                    'token_expires_at': 42.0,
                },
            }
        }
        async with async_session_maker() as session:
            stored = (
                await session.execute(
                    text(
                        'SELECT org_mcp_oauth_states FROM org_member '
                        'WHERE user_id = :id'
                    ),
                    {'id': MEMBER_ID},
                )
            ).scalar_one()
        assert 'mine' not in stored  # encrypted at rest

        await store.clear_member_oauth_state(member, 'jira')
        await store.clear_member_oauth_state(member, 'jira')  # idempotent

        assert await store.member_connected_names(member, listings) == set()

    @pytest.mark.asyncio
    async def test_state_only_applies_to_user_managed_oauth_listings(
        self, store, member
    ):
        await store.create('bifrost', _user_managed())
        await store.create(
            'jira-shared',
            OrgMcpServer(managed_by='org', server=_oauth(state=ADMIN_STATE)),
        )

        with pytest.raises(OrgMcpServerNotFoundError):
            await store.set_member_oauth_state(member, 'ghost', _state('x'))
        with pytest.raises(OrgMcpServerConnectionError):
            await store.set_member_oauth_state(member, 'bifrost', _state('x'))
        with pytest.raises(OrgMcpServerConnectionError):
            await store.set_member_oauth_state(member, 'jira-shared', _state('x'))
        with pytest.raises(OrgMcpServerConnectionError):
            await store.set_member_oauth_state(
                member, 'jira-shared', MCPOAuthState.model_validate({'tokens': {}})
            )
        assert await store.member_connected_names(member, await store.list()) == set()

    @pytest.mark.asyncio
    async def test_non_member_cannot_hold_state(self, store, member):
        await store.create('jira', OrgMcpServer(managed_by='user', server=_oauth()))

        with pytest.raises(OrgMcpMemberMissingError):
            await store.set_member_oauth_state(uuid4(), 'jira', _state('x'))
        assert await store.member_connected_names(uuid4(), await store.list()) == set()

    @pytest.mark.asyncio
    async def test_launch_view_injects_the_members_tokens(
        self, store, member, async_session_maker
    ):
        await store.create('jira', OrgMcpServer(managed_by='user', server=_oauth()))
        await store.create(
            'confluence',
            OrgMcpServer(
                managed_by='user', server=_oauth('https://confluence.example/mcp')
            ),
        )
        await store.create(
            'jira-shared',
            OrgMcpServer(managed_by='org', server=_oauth(state=ADMIN_STATE)),
        )
        await store.set_member_oauth_state(member, 'jira', _state('mine'))
        org = await _read_org(async_session_maker)
        org_member = await _read_member(async_session_maker)

        launch = org_launch_mcp_servers(org, org_member)
        via_profiles = launch_mcp_config(org, org_member)
        anonymous = org_launch_mcp_servers(org, None)

        assert set(launch) == {'jira', 'confluence', 'jira-shared'}
        assert (
            launch['jira'].oauth_auth.state.tokens.access_token.get_secret_value()
            == 'mine'
        )
        # The client secret rides along for the token refresh.
        assert (
            launch['jira'].oauth_auth.authentication.client_secret.get_secret_value()
            == 'client-secret'
        )
        # Not authorized by this member: no tokens, so the launch leaves it out.
        assert launch['confluence'].oauth_auth.state is None
        # The org's own tokens stay the org's.
        assert (
            launch[
                'jira-shared'
            ].oauth_auth.state.tokens.access_token.get_secret_value()
            == 'admin-access'
        )
        assert set(via_profiles) == set(launch)
        assert anonymous['jira'].oauth_auth.state is None

    @pytest.mark.asyncio
    async def test_write_back_by_url_updates_the_listing_or_the_member(
        self, store, member, async_session_maker
    ):
        await store.create('jira', OrgMcpServer(managed_by='user', server=_oauth()))
        await store.create(
            'jira-shared',
            OrgMcpServer(managed_by='org', server=_oauth(state=ADMIN_STATE)),
        )
        await store.create('datadog', _org_managed())
        await store.set_member_oauth_state(member, 'jira', _state('mine'))

        # A sandbox reports the URL with a trailing slash, as FastMCP keys it.
        written = await store.write_back_oauth_state(
            member, JIRA_URL + '/', _state('refreshed')
        )

        assert sorted(written) == ['jira', 'jira-shared']
        listings = await store.list()
        assert (
            listings[
                'jira-shared'
            ].server.oauth_auth.state.tokens.access_token.get_secret_value()
            == 'refreshed'
        )
        # The listing itself never takes a member's tokens.
        assert listings['jira'].server.oauth_auth.state is None
        org_member = await _read_member(async_session_maker)
        launch = org_launch_mcp_servers(
            await _read_org(async_session_maker), org_member
        )
        assert (
            launch['jira'].oauth_auth.state.tokens.access_token.get_secret_value()
            == 'refreshed'
        )

    @pytest.mark.asyncio
    async def test_write_back_skips_unknown_urls_and_unauthorized_members(
        self, store, member
    ):
        await store.create('jira', OrgMcpServer(managed_by='user', server=_oauth()))
        await store.create('datadog', _org_managed())

        unknown = await store.write_back_oauth_state(
            member, 'https://elsewhere.example/mcp', _state('x')
        )
        bearer = await store.write_back_oauth_state(member, DATADOG_URL, _state('x'))
        # The member never authorized ``jira``: a refresh cannot connect them.
        never_authorized = await store.write_back_oauth_state(
            member, JIRA_URL, _state('x')
        )
        stranger = await store.write_back_oauth_state(uuid4(), JIRA_URL, _state('x'))

        assert unknown == bearer == never_authorized == stranger == []
        assert await store.member_connected_names(member, await store.list()) == set()

    @pytest.mark.asyncio
    async def test_deleting_a_listing_forgets_every_members_tokens(
        self, store, member, async_session_maker
    ):
        await store.create('jira', OrgMcpServer(managed_by='user', server=_oauth()))
        await store.create(
            'confluence',
            OrgMcpServer(
                managed_by='user', server=_oauth('https://confluence.example/mcp')
            ),
        )
        await store.set_member_oauth_state(member, 'jira', _state('mine'))
        await store.set_member_oauth_state(member, 'confluence', _state('other'))

        await store.delete('jira')

        raw = (await _read_member(async_session_maker)).org_mcp_oauth_states
        assert set(raw) == {'confluence'}

    @pytest.mark.asyncio
    async def test_former_member_cannot_overwrite_the_shared_tokens(
        self, store, member
    ):
        """A sandbox can outlive its owner's membership."""
        await store.create(
            'jira-shared',
            OrgMcpServer(managed_by='org', server=_oauth(state=ADMIN_STATE)),
        )

        written = await store.write_back_oauth_state(uuid4(), JIRA_URL, _state('evil'))

        assert written == []
        shared = (await store.get('jira-shared')).server.oauth_auth.state
        assert shared.tokens.access_token.get_secret_value() == 'admin-access'

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'repointed',
        [
            _oauth('https://other-host.example/mcp'),
            MCPServer.model_validate(
                {
                    'url': JIRA_URL,
                    'transport': 'http',
                    'auth': {
                        'strategy': 'oauth2',
                        'authentication': {'type': 'oauth', 'client_id': 'other'},
                    },
                }
            ),
        ],
        ids=['another-url', 'another-client'],
    )
    async def test_tokens_do_not_follow_a_repointed_listing(
        self, store, member, async_session_maker, repointed
    ):
        """An admin changes a listing's URL or client: a member's tokens were
        issued for the old one and must never reach the new one."""
        await store.create('jira', OrgMcpServer(managed_by='user', server=_oauth()))
        await store.set_member_oauth_state(member, 'jira', _state('mine'))

        await store.update('jira', OrgMcpServer(managed_by='user', server=repointed))

        listings = await store.list()
        assert await store.member_connected_names(member, listings) == set()
        launch = org_launch_mcp_servers(
            await _read_org(async_session_maker),
            await _read_member(async_session_maker),
        )
        assert launch['jira'].oauth_auth.state is None
        # A refresh reported for the new endpoint does not revive the old tokens.
        written = await store.write_back_oauth_state(
            member, repointed.url, _state('stale')
        )
        assert written == []
