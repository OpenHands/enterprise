"""SaasUserAuth.get_provider_connections() against a real database.

App-server code resolves linked LLM profiles with these connections, so they
must come from the request's effective org, and only for a member of it (the
same rule as the settings load that supplies the profiles).
"""

import uuid
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.app_server.settings.provider_connections import (
    ProviderConnection,
    ProviderConnections,
)
from server.auth.saas_user_auth import SaasUserAuth
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User

CONNECTIONS = ProviderConnections(
    connections={
        'proxy': ProviderConnection(
            id='proxy',
            display_name='Proxy',
            api_key=SecretStr('sk-connection-key'),
            base_url='https://llm-proxy.example.test',
        )
    }
)


async def _arrange(session_maker, *, member: bool) -> tuple[str, uuid.UUID]:
    org_id = uuid.uuid4()
    user_id = str(uuid.uuid4())
    async with session_maker() as session:
        role = Role(name='member', rank=3)
        session.add(role)
        session.add(
            Org(
                id=org_id,
                name='Test Organization',
                org_version=1,
                enable_proactive_conversation_starters=True,
                provider_connections=CONNECTIONS.model_dump(
                    mode='json', context={'expose_secrets': True}
                ),
            )
        )
        await session.flush()
        session.add(
            User(
                id=uuid.UUID(user_id),
                current_org_id=org_id,
                user_consents_to_analytics=True,
            )
        )
        await session.flush()
        if member:
            session.add(
                OrgMember(
                    org_id=org_id,
                    user_id=uuid.UUID(user_id),
                    role_id=role.id,
                    status='active',
                    llm_api_key='sk-member-key',
                )
            )
        await session.commit()
    return user_id, org_id


def _patched(session_maker):
    return (
        patch('storage.user_store.a_session_maker', session_maker),
        patch('storage.org_store.a_session_maker', session_maker),
        patch('storage.org_member_store.a_session_maker', session_maker),
        patch('storage.role_store.a_session_maker', session_maker),
    )


@pytest.mark.asyncio
async def test_member_gets_the_effective_org_connections(async_session_maker):
    user_id, _ = await _arrange(async_session_maker, member=True)
    user_auth = SaasUserAuth(
        user_id=user_id, refresh_token=SecretStr('mock_refresh_token')
    )

    p1, p2, p3, p4 = _patched(async_session_maker)
    with p1, p2, p3, p4:
        connections = await user_auth.get_provider_connections()

    connection = connections.get('proxy')
    assert connection is not None
    assert connection.api_key_value() == 'sk-connection-key'
    assert connection.base_url == 'https://llm-proxy.example.test'


@pytest.mark.asyncio
async def test_non_member_gets_no_connections(async_session_maker):
    """A caller without a member row (e.g. a super admin) gets no keys."""
    user_id, _ = await _arrange(async_session_maker, member=False)
    user_auth = SaasUserAuth(
        user_id=user_id, refresh_token=SecretStr('mock_refresh_token')
    )

    p1, p2, p3, p4 = _patched(async_session_maker)
    with p1, p2, p3, p4:
        connections = await user_auth.get_provider_connections()

    assert connections.all() == []
