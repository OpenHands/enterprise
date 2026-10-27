from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, Request
from pydantic import SecretStr

from tests import postgres_testdb

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path(
    os.environ.get('QA_REPORT', str(ROOT / '.pr' / 'agent-profile-http-qa.json'))
)
RUNTIME_URL = os.environ['AGENT_SERVER_URL']
RUNTIME_KEY = os.environ['SESSION_API_KEY']
ORG_ID, USER_ID = uuid4(), uuid4()
AUTH_KEY = secrets.token_urlsafe(24)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def main():
    pg = postgres_testdb.start_server()
    database = postgres_testdb.create_test_database(pg.server, pg.template)
    os.environ.update(
        DB_HOST=pg.server.host,
        DB_PORT=str(pg.server.port),
        DB_USER=pg.server.user,
        DB_PASS=pg.server.password,
        DB_NAME=database,
        DB_DRIVER='pg8000',
        ENABLE_LEGACY_LITELLM='false',
        ENABLE_MARKETPLACE_PLUGIN_LOADING='false',
        LITE_LLM_API_URL='https://llm-proxy.app.all-hands.dev',
    )
    # Import the complete SQLAlchemy model registry before creating ORM rows.
    import tests.unit.conftest  # noqa: F401
    from openhands.app_server.app_conversation.app_conversation_models import AgentType
    from openhands.app_server.app_conversation.live_status_app_conversation_service import (
        LiveStatusAppConversationService,
    )
    from openhands.app_server.integrations.provider import CustomSecret
    from openhands.app_server.secrets.secrets_models import Secrets
    from openhands.app_server.settings.settings_models import Settings
    from openhands.app_server.user.auth_user_context import AuthUserContext
    from openhands.app_server.user_auth import get_user_id
    from openhands.sdk.llm import LLMProfileStore
    from openhands.sdk.mcp.config import MCPServer
    from openhands.sdk.utils.cipher import Cipher
    from server.auth.org_context import resolve_effective_org_id
    from server.routes.agent_profiles import router
    from storage.database import session_maker
    from storage.org import Org
    from storage.org_member import OrgMember
    from storage.role import Role
    from storage.saas_settings_store import SaasSettingsStore
    from storage.user import User

    profile_dir = Path(
        os.environ.get('QA_LLM_PROFILE_DIR', str(Path.home() / '.openhands/profiles'))
    )
    key_file = Path(
        os.environ.get(
            'QA_CIPHER_KEY_FILE',
            str(profile_dir.parent / 'agent-canvas/secret-key.txt'),
        )
    )
    cipher = Cipher(key_file.read_text().strip()) if key_file.exists() else None
    if os.environ.get('QA_LLM_JSON'):
        from openhands.sdk import LLM

        llm = LLM.model_validate_json(os.environ['QA_LLM_JSON'])
    else:
        llm = LLMProfileStore(base_dir=profile_dir).load(
            os.environ.get('QA_LLM_PROFILE', 'sonnet'), cipher=cipher
        )
    assert llm.api_key is not None and not llm.api_key.get_secret_value().startswith(
        'gAAAA'
    )
    llm = llm.model_copy(update={'stream': False, 'max_output_tokens': 600})
    with session_maker() as session:
        session.add(Role(id=20, name='admin', rank=2))
        session.flush()
        session.add(
            Org(
                id=ORG_ID,
                name='profile-http-qa',
                org_version=1,
                llm_profiles={
                    'profiles': {
                        'qa': llm.model_dump(
                            mode='json', context={'expose_secrets': True}
                        )
                    },
                    'active': 'qa',
                },
            )
        )
        session.add(
            User(id=USER_ID, current_org_id=ORG_ID, user_consents_to_analytics=True)
        )
        session.flush()
        session.add(
            OrgMember(
                org_id=ORG_ID,
                user_id=USER_ID,
                role_id=20,
                status='active',
                llm_api_key=llm.api_key.get_secret_value(),
                agent_settings_diff={},
                conversation_settings_diff={},
            )
        )
        session.commit()

    fixture = ROOT / '.pr' / 'qa_echo.py'
    fixture.write_text(
        'from fastmcp import FastMCP\nmcp=FastMCP("Enterprise QA")\n@mcp.tool()\ndef qa_echo(value: str) -> str:\n    return value\nmcp.run(show_banner=False)\n'
    )
    mcp = MCPServer(command=str(ROOT / '.venv/bin/python'), args=[str(fixture)])
    with session_maker() as session:
        member = (
            session.query(OrgMember).filter_by(org_id=ORG_ID, user_id=USER_ID).one()
        )
        member.mcp_config = {'qa': mcp.model_dump(mode='json')}
        session.commit()

    class LocalAuth:
        async def get_user_id(self):
            return str(USER_ID)

        async def get_user_email(self):
            return None

        async def get_effective_org_id(self):
            return ORG_ID

        async def get_user_settings(self, **kwargs):
            return await SaasSettingsStore(str(USER_ID), effective_org_id=ORG_ID).load(
                **kwargs
            )

        async def get_provider_tokens(self):
            return {}

        async def get_mcp_api_key(self):
            return None

        async def get_secrets(self):
            return Secrets(
                custom_secrets={
                    'ALLOWED_QA': CustomSecret(secret=SecretStr('allowed-qa-value')),
                    'DENIED_QA': CustomSecret(secret=SecretStr('denied-qa-value')),
                }
            )

        def invalidate_user_settings_cache(self):
            pass

    app = FastAPI()
    app.include_router(router)
    runtime_headers = {'X-Session-API-Key': RUNTIME_KEY}
    runtime_client = httpx.AsyncClient(timeout=90)
    launches = []

    @app.middleware('http')
    async def identity_fixture(request: Request, call_next):
        if request.headers.get('Authorization') != f'Bearer {AUTH_KEY}':
            return __import__(
                'starlette.responses', fromlist=['JSONResponse']
            ).JSONResponse({'detail': 'Unauthorized'}, status_code=401)
        request.state.user_auth = LocalAuth()
        return await call_next(request)

    app.dependency_overrides[get_user_id] = lambda: str(USER_ID)
    app.dependency_overrides[resolve_effective_org_id] = lambda: ORG_ID

    @app.post('/_qa/launch')
    async def launch(body: dict):
        context = AuthUserContext(user_auth=LocalAuth())
        user = await context.get_user_info(
            resolve_agent_profile=True, override_agent_profile_id=body.get('profile_id')
        )
        service = LiveStatusAppConversationService(
            init_git_in_empty_workspace=False,
            user_context=context,
            app_conversation_info_service=None,
            app_conversation_start_task_service=None,
            event_callback_service=None,
            event_service=None,
            sandbox_service=None,
            sandbox_spec_service=None,
            jwt_service=None,
            pending_message_service=None,
            sandbox_startup_timeout=30,
            sandbox_startup_poll_frequency=1,
            max_num_conversations_per_sandbox=20,
            httpx_client=runtime_client,
            web_url=None,
            openhands_provider_base_url=None,
            access_token_hard_timeout=None,
        )
        browser_available = await service._ensure_profile_runtime(
            RUNTIME_URL, RUNTIME_KEY
        )
        if body.get('child_launcher'):
            service.enable_child_conversation_launcher = True
            service.web_url = 'https://qa.example.com'
        user.conversation_settings = user.conversation_settings.model_copy(
            update={'autotitle': False}
        )
        request = await service._build_start_conversation_request_for_user(
            user=user,
            sandbox=SimpleNamespace(),
            conversation_id=uuid4(),
            initial_message=None,
            system_message_suffix='RUNTIME_SUFFIX',
            git_provider=None,
            working_dir=os.environ.get('QA_WORKSPACE', str(ROOT / '.pr' / 'workspace')),
            agent_type=AgentType(body.get('agent_type', 'default')),
            browser_available=browser_available,
            api_secrets={'DENIED_API_QA': SecretStr('denied-api-value')},
        )
        payload = request.model_dump(mode='json', context={'expose_secrets': True})
        response = await runtime_client.post(
            f'{RUNTIME_URL}/api/conversations', json=payload, headers=runtime_headers
        )
        response.raise_for_status()
        data = response.json()
        agent = data['agent']
        sandbox_security_policy = None
        if agent['kind'] == 'Agent':
            from openhands.sdk import Agent

            runtime_agent = Agent.model_validate(agent)
            prompt = runtime_agent.static_system_message
            sandbox_security_policy = (
                'Container-scoped edits and installs.' in prompt
                and 'Project-scoped edits or execution.' not in prompt
            )
            assert sandbox_security_policy
        runtime_secret_names = sorted(data['secret_registry']['secret_sources'])
        assert runtime_secret_names == sorted(request.secrets)
        record = {
            'id': data['id'],
            'status': response.status_code,
            'wire_source': 'agent_settings' if request.agent is None else 'agent',
            'profile_id': user.active_agent_profile_id,
            'revision': user.active_agent_profile_revision,
            'request_secret_names': sorted(request.secrets),
            'runtime_secret_names': runtime_secret_names,
            'agent_kind': agent['kind'],
            'tools': [t['name'] for t in agent.get('tools', [])],
            'include_default_tools': agent.get('include_default_tools'),
            'sandbox_security_policy': sandbox_security_policy,
            'browser_available': browser_available,
            'mcp_servers': sorted(agent.get('mcp_config', {})),
            'persona': agent.get('persona'),
            'suffix': (agent.get('agent_context') or {}).get('system_message_suffix'),
        }
        launches.append(record)
        return record

    @app.get('/_qa/settings')
    async def readable_settings():
        settings = await SaasSettingsStore(str(USER_ID), effective_org_id=ORG_ID).load()
        return {'readable': isinstance(settings, Settings)}

    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error')
    )
    server_task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    results = {
        'sdk_version': version('openhands-sdk'),
        'identity': 'Local bearer identity fixture; real org permissions, PostgreSQL, router, resolver and builder. No deployed Keycloak or Cloud orchestration.',
        'cases': [],
    }
    conversations = []
    try:
        async with httpx.AsyncClient(
            base_url=f'http://127.0.0.1:{port}',
            headers={'Authorization': f'Bearer {AUTH_KEY}'},
            timeout=120,
        ) as client:
            response = await client.get('/api/agent-profiles')
            response.raise_for_status()
            assert response.json()['profiles'] == []
            profile = {
                'agent_kind': 'openhands',
                'llm_profile_ref': 'qa',
                'tools': [],
                'persona': 'Use only the requested tools.',
                'system_message_suffix': 'PROFILE_SUFFIX',
                'mcp_server_refs': ['qa'],
                'secret_refs': ['ALLOWED_QA'],
            }
            for name in ('default', 'default-copy'):
                response = await client.post(
                    f'/api/agent-profiles/{name}', json=profile
                )
                response.raise_for_status()
            listing = (await client.get('/api/agent-profiles')).json()['profiles']
            ids = {p['name']: p['id'] for p in listing}
            for name in ('default', 'default-copy'):
                preview = await client.post(f'/api/agent-profiles/{name}/materialize')
                preview.raise_for_status()
                assert preview.json()['valid'] is True
                response = await client.post(
                    '/_qa/launch', json={'profile_id': ids[name]}
                )
                response.raise_for_status()
                record = response.json()
                conversations.append(record['id'])
                assert record['tools'] == [] and record['mcp_servers'] == ['qa']
                assert record['suffix'] == 'PROFILE_SUFFIX\n\nRUNTIME_SUFFIX'
                assert record['request_secret_names'] == ['ALLOWED_QA']
                assert record['wire_source'] == 'agent'
                results['cases'].append({'name': name, 'preview_valid': True, **record})
            for name, selection in (
                (
                    'coding-no-browser',
                    [{'name': n} for n in ('terminal', 'file_editor', 'task_tracker')],
                ),
                ('default-tools', None),
                ('child-launcher-defaults', None),
            ):
                response = await client.post(
                    f'/api/agent-profiles/{name}',
                    json={**profile, 'tools': selection, 'mcp_server_refs': []},
                )
                response.raise_for_status()
                profile_id = next(
                    p['id']
                    for p in (await client.get('/api/agent-profiles')).json()[
                        'profiles'
                    ]
                    if p['name'] == name
                )
                preview = await client.post(f'/api/agent-profiles/{name}/materialize')
                preview.raise_for_status()
                assert preview.json()['valid'] is True
                response = await client.post(
                    '/_qa/launch',
                    json={
                        'profile_id': profile_id,
                        'child_launcher': name == 'child-launcher-defaults',
                    },
                )
                response.raise_for_status()
                record = response.json()
                conversations.append(record['id'])
                if selection is not None:
                    assert record['tools'] == [t['name'] for t in selection]
                    assert 'browser_tool_set' not in record['tools']
                else:
                    assert 'terminal' in record['tools']
                    assert ('browser_tool_set' in record['tools']) == record[
                        'browser_available'
                    ]
                    assert 'SwitchLLMTool' in record['include_default_tools']
                    if name == 'child-launcher-defaults':
                        assert 'start_child_conversation' in record['tools']
                assert record['persona'] == profile['persona']
                assert record['mcp_servers'] == (
                    ['default'] if name == 'child-launcher-defaults' else []
                )
                results['cases'].append({'name': name, 'preview_valid': True, **record})
            before = len(launches)
            response = await client.post(
                '/api/agent-profiles/broken',
                json={**profile, 'mcp_server_refs': ['missing']},
            )
            response.raise_for_status()
            broken_id = next(
                p['id']
                for p in (await client.get('/api/agent-profiles')).json()['profiles']
                if p['name'] == 'broken'
            )
            response = await client.post('/_qa/launch', json={'profile_id': broken_id})
            assert response.status_code == 422 and len(launches) == before
            results['cases'].append(
                {
                    'name': 'dangling_mcp',
                    'status': 422,
                    'detail': response.json()['detail'],
                    'runtime_launches': 0,
                }
            )
            response = await client.post(
                '/_qa/launch', json={'profile_id': str(uuid4())}
            )
            assert response.status_code == 404 and len(launches) == before
            assert (await client.get('/_qa/settings')).json()['readable']
            results['cases'].append(
                {
                    'name': 'unknown_profile',
                    'status': 404,
                    'settings_readable': True,
                    'runtime_launches': 0,
                }
            )
            response = await client.post(
                '/api/agent-profiles/acp',
                json={
                    'agent_kind': 'acp',
                    'acp_server': 'claude-code',
                    'secret_refs': [],
                    'mcp_server_refs': [],
                },
            )
            response.raise_for_status()
            acp_id = next(
                p['id']
                for p in (await client.get('/api/agent-profiles')).json()['profiles']
                if p['name'] == 'acp'
            )
            response = await client.post('/_qa/launch', json={'profile_id': acp_id})
            response.raise_for_status()
            acp = response.json()
            conversations.append(acp['id'])
            assert acp['agent_kind'] == 'ACPAgent' and acp['request_secret_names'] == []
            assert acp['mcp_servers'] == [] and acp['suffix'] == 'RUNTIME_SUFFIX'
            results['cases'].append({'name': 'acp_create_without_inference', **acp})
            future_id = str(uuid4())
            future = {
                'id': future_id,
                'name': 'future',
                'agent_kind': 'openhands',
                'schema_version': 99,
                'future_option': {'keep': True},
            }
            with session_maker() as session:
                org = session.get(Org, ORG_ID)
                envelope = dict(org.agent_profiles)
                envelope['profiles'] = {**envelope['profiles'], future_id: future}
                envelope['active'] = future_id
                org.agent_profiles = envelope
                session.commit()
            response = await client.post('/api/agent-profiles/default', json=profile)
            response.raise_for_status()
            with session_maker() as session:
                assert (
                    session.get(Org, ORG_ID).agent_profiles['profiles'][future_id]
                    == future
                )
            before = len(launches)
            response = await client.post('/_qa/launch', json={})
            assert response.status_code == 422 and len(launches) == before
            assert (await client.get('/_qa/settings')).json()['readable']
            results['cases'].append(
                {
                    'name': 'opaque_profile_preserved_and_active_launch_rejected',
                    'status': 422,
                    'opaque_preserved': True,
                    'settings_readable': True,
                }
            )
            cid = conversations[0]
            response = await runtime_client.post(
                f'{RUNTIME_URL}/api/conversations/{cid}/events',
                headers=runtime_headers,
                json={
                    'role': 'user',
                    'content': [
                        {
                            'type': 'text',
                            'text': 'Call qa_echo with ENTERPRISE_PROFILE_QA_OK, then finish.',
                        }
                    ],
                    'run': True,
                },
            )
            response.raise_for_status()
            for _ in range(120):
                state = (
                    await runtime_client.get(
                        f'{RUNTIME_URL}/api/conversations/{cid}',
                        headers=runtime_headers,
                    )
                ).json()
                if state['execution_status'] in ('finished', 'error'):
                    break
                await asyncio.sleep(0.5)
            assert state['execution_status'] == 'finished'
            events_response = await runtime_client.get(
                f'{RUNTIME_URL}/api/conversations/{cid}/events/search',
                headers=runtime_headers,
            )
            events_response.raise_for_status()
            events = events_response.json()['items']
            actions = [
                e
                for e in events
                if e.get('kind') == 'ActionEvent' and e.get('tool_name') == 'qa_echo'
            ]
            observations = [
                e
                for e in events
                if e.get('kind') == 'ObservationEvent'
                and e.get('tool_name') == 'qa_echo'
            ]
            assert actions and observations
            assert 'ENTERPRISE_PROFILE_QA_OK' in json.dumps(observations)
            results['cases'].append(
                {
                    'name': 'real_model_mcp',
                    'model': llm.model,
                    'execution_status': 'finished',
                    'tool': 'qa_echo',
                    'marker': 'ENTERPRISE_PROFILE_QA_OK',
                }
            )
        REPORT.write_text(json.dumps(results, indent=2) + '\n')
        print(
            json.dumps(
                {'ok': True, 'cases': len(results['cases']), 'report': str(REPORT)}
            )
        )
    finally:
        for cid in conversations:
            await runtime_client.delete(
                f'{RUNTIME_URL}/api/conversations/{cid}', headers=runtime_headers
            )
        await runtime_client.aclose()
        server.should_exit = True
        await server_task
        pg.stop()


if __name__ == '__main__':
    asyncio.run(main())
