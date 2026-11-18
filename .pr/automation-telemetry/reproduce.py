"""Local-only provenance reproduction. No enterprise or SDK source is modified."""

# Configure loopback-only networking and per-run persistence before SDK imports.
# ruff: noqa: E402

import argparse
import asyncio
import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
ENTERPRISE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ENTERPRISE))
os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['OH_TELEMETRY_CONSENT'] = 'granted'
os.environ['OH_TELEMETRY_CONSENT_MODE'] = 'override'
os.environ.pop('SESSION_API_KEY', None)
# Refuse all non-loopback sockets, including optional integrations/LLM/analytics.
_original_connect = socket.socket.connect


def local_connect(sock, address):
    if isinstance(address, tuple) and address[0] not in (
        '127.0.0.1',
        '::1',
        'localhost',
    ):
        raise RuntimeError(f'External network prohibited: {address}')
    return _original_connect(sock, address)


socket.socket.connect = local_connect

# Set persistence/config paths before any SDK singleton can initialize.
_boot_output = Path(sys.argv[sys.argv.index('--output') + 1]).resolve()
os.environ['OH_PERSISTENCE_DIR'] = str(_boot_output / 'settings')
os.environ['OH_ENABLE_VSCODE'] = 'false'
os.environ['OH_PRELOAD_TOOLS'] = 'false'
os.environ['OH_CONVERSATIONS_PATH'] = str(_boot_output / 'conversations')
os.environ['OH_WORKSPACE_PATH'] = str(_boot_output / 'workspace')
os.environ['OH_BASH_EVENTS_DIR'] = str(_boot_output / 'bash-events')
import httpx
import uvicorn
from fastapi import FastAPI, Request

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config, TelemetrySpec


def write(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + '\n')


async def run(out, expect, baseline):
    out.mkdir(parents=True, exist_ok=True)
    (out / 'workspace').mkdir(exist_ok=True)
    os.environ['OH_PERSISTENCE_DIR'] = str(out / 'settings')
    os.environ['TMUX_TMPDIR'] = str(out / 'tmux')
    os.environ['OH_ENABLE_VSCODE'] = 'false'
    os.environ['OH_PRELOAD_TOOLS'] = 'false'
    events = []
    llm_calls = []
    collector = FastAPI()

    @collector.middleware('http')
    async def prohibit_llm(request, call_next):
        if request.url.path.startswith('/llm-must-not-be-called'):
            llm_calls.append(request.url.path)
            raise AssertionError('LLM execution forbidden')
        return await call_next(request)

    @collector.post('/capture')
    async def capture(request: Request):
        batch = await request.json()
        events.extend(batch['events'])
        write(out / 'telemetry.json', events)
        return {'ok': True}

    # Ports assigned by the OS and actual TCP HTTP (not TestClient/ASGITransport).
    servers, tasks, sockets = [], [], []

    async def serve(app):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        sock.listen(128)
        sockets.append(sock)
        server = uvicorn.Server(uvicorn.Config(app, log_level='warning', lifespan='on'))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        servers.append(server)
        tasks.append(task)
        for _ in range(300):
            if server.started:
                return f'http://127.0.0.1:{sock.getsockname()[1]}'
            if task.done():
                await task
                raise RuntimeError('server exited')
            await asyncio.sleep(0.1)
        raise TimeoutError('startup')

    try:
        capture_url = await serve(collector)
        config = Config(
            session_api_keys=['local-dummy-session-key'],
            conversations_path=out / 'conversations',
            workspace_path=out / 'workspace',
            bash_events_dir=out / 'bash-events',
            enable_vscode=False,
            preload_tools=False,
            telemetry=TelemetrySpec(
                exporter='http',
                http_endpoint=capture_url + '/capture',
                event_buffer_size=1,
                flush_delay=0.1,
            ),
        )
        agent_app = create_app(config)
        wire = []

        @agent_app.middleware('http')
        async def record_wire(request, call_next):
            if request.method == 'POST' and request.url.path == '/api/conversations':
                wire.append(await request.json())
                write(out / 'agent-server-request.json', wire)
            return await call_next(request)

        agent_url = await serve(agent_app)

        if baseline:
            import importlib.util
            import subprocess

            name = 'openhands.app_server.app_conversation.live_status_app_conversation_service'
            source_file = out / 'baseline_service.py'
            source_file.write_bytes(
                subprocess.check_output(
                    [
                        'git',
                        '-C',
                        str(ENTERPRISE),
                        'show',
                        'b0d455745:openhands/app_server/app_conversation/live_status_app_conversation_service.py',
                    ]
                )
            )
            spec = importlib.util.spec_from_file_location(name, source_file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        from pydantic import SecretStr

        from openhands.app_server.app_conversation import (
            app_conversation_router as routes,
        )
        from openhands.app_server.app_conversation.live_status_app_conversation_service import (
            LiveStatusAppConversationService,
        )
        from openhands.app_server.sandbox.sandbox_models import (
            AGENT_SERVER,
            ExposedUrl,
            SandboxInfo,
            SandboxStatus,
        )
        from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
        from openhands.app_server.settings.provider_connections import (
            ProviderConnections,
        )
        from openhands.app_server.user.user_models import UserInfo
        from openhands.sdk import LLM
        from openhands.sdk.settings import OpenHandsAgentSettings

        user = UserInfo(
            id='local-attribution-fixture',
            agent_settings=OpenHandsAgentSettings(
                llm=LLM(
                    model='openai/gpt-4o-mini',
                    api_key=SecretStr('dummy-no-paid-llm'),
                    base_url=capture_url + '/llm-must-not-be-called',
                ),
                tools=[],
            ),
            registered_marketplaces=[],
        )

        class UserFixture:
            async def get_user_id(self):
                return user.id

            async def get_user_info(self, **kwargs):
                return user

            async def get_user_email(self):
                return None

            async def get_provider_connections(self):
                return ProviderConnections()

            async def get_provider_tokens(self, **kwargs):
                return {}

            async def get_latest_token(self, *args, **kwargs):
                return None

            async def get_secrets(self):
                return {}

            async def get_mcp_api_key(self):
                return None

            async def get_user_git_info(self):
                return None

            async def get_named_oauth_secrets(self, *args, **kwargs):
                from openhands.app_server.user.user_models import NamedOAuthSecrets

                return NamedOAuthSecrets()

        context = UserFixture()
        sandbox = SandboxInfo(
            id='local-fixture',
            created_by_user_id=user.id,
            sandbox_spec_id='local-fixture',
            status=SandboxStatus.RUNNING,
            session_api_key='local-dummy-session-key',
            exposed_urls=[
                ExposedUrl(
                    name=AGENT_SERVER,
                    url=agent_url,
                    port=int(agent_url.rsplit(':', 1)[1]),
                )
            ],
        )

        class SandboxFixture:
            async def get_sandbox(self, *args):
                return sandbox

            async def wait_for_sandbox_running(self, *args, **kwargs):
                return sandbox

        class SpecFixture:
            async def resolve_existing_sandbox_spec(self, *args):
                return SandboxSpecInfo(
                    id='local-fixture', command=None, working_dir=str(out / 'workspace')
                )

        class RecordFixture:
            def __init__(self):
                self.info = None
                self.tasks = []

            async def save_app_conversation_info(self, info):
                self.info = info
                write(out / 'app-record.json', info.model_dump(mode='json'))

            async def save_app_conversation_start_task(self, task):
                self.tasks.append(task.model_dump(mode='json'))
                write(out / 'start-tasks.json', self.tasks)

            async def save_event_callback(self, callback):
                raise AssertionError('No callbacks expected with explicit title')

        records = RecordFixture()

        class EmptyPending:
            async def update_conversation_id(self, **kwargs):
                return 0

            async def get_pending_messages(self, *args):
                return []

            async def search_pending_messages(self, **kwargs):
                return SimpleNamespace(items=[])

        class SessionFixture:
            async def close(self):
                pass

        # Setup is a sandbox provisioning boundary only; the builder remains untouched.
        class LocalService(LiveStatusAppConversationService):
            async def run_setup_scripts(self, *args, **kwargs):
                if False:
                    yield

        client = httpx.AsyncClient(trust_env=False)
        service = LocalService(
            user_context=context,
            app_conversation_info_service=records,
            app_conversation_start_task_service=records,
            event_callback_service=records,
            event_service=None,
            sandbox_service=SandboxFixture(),
            sandbox_spec_service=SpecFixture(),
            jwt_service=None,
            pending_message_service=EmptyPending(),
            sandbox_startup_timeout=20,
            sandbox_startup_poll_frequency=1,
            max_num_conversations_per_sandbox=10,
            httpx_client=client,
            web_url=None,
            openhands_provider_base_url=None,
            access_token_hard_timeout=None,
            init_git_in_empty_workspace=False,
        )
        app = FastAPI()
        app.include_router(routes.router, prefix='/api/v1')
        app.dependency_overrides[routes.user_context_dependency.dependency] = lambda: (
            context
        )
        app.dependency_overrides[routes.get_secrets_store] = lambda: None
        app.dependency_overrides[routes.db_session_dependency.dependency] = lambda: (
            SessionFixture()
        )
        app.dependency_overrides[routes.httpx_client_dependency.dependency] = lambda: (
            client
        )
        app.dependency_overrides[
            routes.app_conversation_service_dependency.dependency
        ] = lambda: service
        app_url = await serve(app)
        source = {
            'sandbox_id': sandbox.id,
            'title': 'Local automation attribution repro',
            'generate_title_on_submit': False,
            'trigger': 'automation',
            'tags': {
                'automationtrigger': 'automation',
                'automationid': 'local-automation',
                'automationrunid': 'local-run',
            },
        }
        write(out / 'app-request.json', source)
        async with httpx.AsyncClient(trust_env=False) as caller:
            response = await caller.post(
                app_url + '/api/v1/app-conversations', json=source
            )
            write(
                out / 'app-response.json',
                {'status': response.status_code, 'body': response.json()},
            )
            response.raise_for_status()
            for _ in range(300):
                terminal = records.tasks and records.tasks[-1]['status'] in (
                    'READY',
                    'ERROR',
                )
                created = [
                    e
                    for e in events
                    if e['event'] == 'agent_server.conversation_created'
                ]
                if terminal and (created or records.tasks[-1]['status'] == 'ERROR'):
                    break
                await asyncio.sleep(0.1)
            assert records.tasks[-1]['status'] == 'READY', records.tasks[-1]
            assert len(created) == 1, events
            cid = records.info.id
            runtime_response = await caller.get(
                agent_url + f'/api/conversations/{cid}',
                headers={'X-Session-API-Key': sandbox.session_api_key},
            )
            write(
                out / 'runtime-response.json',
                {
                    'status': runtime_response.status_code,
                    'body': runtime_response.json(),
                },
            )
            runtime_response.raise_for_status()
        result = {
            'app_http_status': response.status_code,
            'terminal_task': records.tasks[-1]['status'],
            'app_record_tags': records.info.tags,
            'wire_tags': wire[0].get('tags'),
            'is_automation': created[0]['properties']['is_automation'],
            'conversation_created_count': len(created),
            'llm_http_calls': len(llm_calls),
            'runtime_execution_status': runtime_response.json().get('execution_status'),
            'runtime_tags': runtime_response.json().get('tags'),
            'baseline_service_from_git': baseline,
            'enterprise_commit': os.popen(f'git -C {ENTERPRISE} rev-parse HEAD')
            .read()
            .strip(),
        }
        write(out / 'result.json', result)
        print(json.dumps(result, indent=2))
        assert result['llm_http_calls'] == 0
        assert result['runtime_execution_status'] == 'idle'
        for key, value in source['tags'].items():
            assert records.info.tags[key] == value
        assert result['is_automation'] is (expect == 'true'), result
    finally:
        for server in reversed(servers):
            server.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expect', choices=['true', 'false'], default='false')
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()
    asyncio.run(run(args.output.resolve(), args.expect, args.baseline))
