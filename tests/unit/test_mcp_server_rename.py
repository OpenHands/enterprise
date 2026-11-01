from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from server.routes.mcp_servers import RenameMcpServerRequest, rename_mcp_server
from storage.org_member import OrgMember
from storage.role import Role


@pytest.fixture
def members(create_org, create_user, session_maker):
    org = create_org()
    other_org = create_org()
    user = create_user(current_org_id=org.id)
    other_user = create_user(current_org_id=org.id)
    with session_maker() as session:
        session.add(Role(id=20, name='member', rank=3))
        for org_id, user_id in [
            (org.id, user.id),
            (other_org.id, user.id),
            (org.id, other_user.id),
        ]:
            session.add(
                OrgMember(
                    org_id=org_id,
                    user_id=user_id,
                    role_id=20,
                    llm_api_key='',
                    status='active',
                    mcp_config={
                        'shttp': {
                            'transport': 'http',
                            'url': 'https://example.com/mcp',
                            'auth': {'strategy': 'bearer', 'value': '${SONAR_TOKEN}'},
                            'headers': {'X-Private': 'stored-secret'},
                            'timeout': 45,
                            'enabled': False,
                        },
                        'sse': {
                            'transport': 'sse',
                            'url': 'https://other.example.com/sse',
                        },
                    },
                )
            )
        session.commit()
    return org.id, user.id


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_rename_preserves_credentials_and_member_scope(
    members, async_session_maker, legacy
):
    org_id, user_id = members
    async with async_session_maker() as session:
        rows = (await session.scalars(select(OrgMember))).all()
        before = {(r.org_id, r.user_id): deepcopy(r.mcp_config) for r in rows}
        member = next(r for r in rows if (r.org_id, r.user_id) == members)
        if legacy:
            member.agent_settings_diff = {
                'mcp_config': member.mcp_config,
                'other': 'keep',
            }
            member.mcp_config = None
            await session.commit()
    with patch('server.routes.mcp_servers.a_session_maker', async_session_maker):
        result = await rename_mcp_server(
            'shttp', RenameMcpServerRequest(new_name='sonarqube'), org_id, str(user_id)
        )
    assert result == {'name': 'sonarqube'}
    async with async_session_maker() as session:
        for member in (await session.scalars(select(OrgMember))).all():
            key = (member.org_id, member.user_id)
            old_servers = before[key]
            if key != members:
                assert member.mcp_config == before[key]
                continue
            assert set(member.mcp_config) == {'sonarqube', 'sse'}
            assert member.mcp_config['sonarqube'] == old_servers['shttp']


@pytest.mark.asyncio
async def test_conflict_and_missing_server_do_not_write(members, async_session_maker):
    org_id, user_id = members
    with patch('server.routes.mcp_servers.a_session_maker', async_session_maker):
        for name, new_name, code in [('shttp', 'SSE', 409), ('missing', 'new', 404)]:
            with pytest.raises(HTTPException) as exc:
                await rename_mcp_server(
                    name,
                    RenameMcpServerRequest(new_name=new_name),
                    org_id,
                    str(user_id),
                )
            assert exc.value.status_code == code
    async with async_session_maker() as session:
        member = await session.scalar(
            select(OrgMember).where(
                OrgMember.org_id == org_id, OrgMember.user_id == user_id
            )
        )
        assert set(member.mcp_config) == {'shttp', 'sse'}


@pytest.mark.asyncio
async def test_edit_and_rename_restore_masks_without_dropping_siblings(
    members, async_session_maker
):
    org_id, user_id = members
    with patch('server.routes.mcp_servers.a_session_maker', async_session_maker):
        await rename_mcp_server(
            'shttp',
            RenameMcpServerRequest(
                new_name='sonar',
                server={
                    'transport': 'http',
                    'url': 'https://example.com/mcp',
                    'auth': {'strategy': 'bearer', 'value': '**********'},
                    'headers': {'X-Private': '**********'},
                    'timeout': 90,
                    'enabled': False,
                },
            ),
            org_id,
            str(user_id),
        )
    async with async_session_maker() as session:
        member = await session.scalar(
            select(OrgMember).where(
                OrgMember.org_id == org_id, OrgMember.user_id == user_id
            )
        )
        assert member.mcp_config['sonar']['auth']['value'] == '${SONAR_TOKEN}'
        assert member.mcp_config['sonar']['headers']['X-Private'] == 'stored-secret'
        assert member.mcp_config['sonar']['timeout'] == 90
        assert member.mcp_config['sonar']['enabled'] is False
        assert 'sse' in member.mcp_config
