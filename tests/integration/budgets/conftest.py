from __future__ import annotations

import os
import platform
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network

from tests import postgres_testdb
from tests.integration.budgets.adapter import (
    BudgetAdapterFactory,
    BudgetTestAdapter,
    UpgradeBudgetAdapterFactory,
    UpgradeBudgetTestAdapter,
)
from tests.integration.budgets.services import (
    LocalAppServer,
    ProviderState,
    ProxyState,
    create_provider_app,
    create_proxy_app,
)

LITELLM_IMAGE = os.environ.get(
    'BUDGET_LITELLM_IMAGE',
    'ghcr.io/berriai/litellm-database:1.100.1@sha256:'
    'fc44cf7f72786e636dc4dc1032b4e431818abfec9d54b8e04284fdea7ef03e2a',
)
AUTH_CACHE_TTL = int(os.environ.get('BUDGET_AUTH_CACHE_TTL', '0'))
MASTER_KEY = 'sk-budget-test-master-key'
BOOTSTRAP_TEAM_ID = 'budget-test-bootstrap-team'


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        'markers', 'budget_known_issue(issue): nonblocking regression linked to Linear'
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        marker = item.get_closest_marker('budget_known_issue')
        if marker is not None:
            if len(marker.args) != 1 or not str(marker.args[0]).startswith('OHE-'):
                raise pytest.UsageError('budget_known_issue requires an OHE issue')
            item.user_properties.append(('budget_issue', marker.args[0]))


@dataclass(frozen=True)
class LiteLlmEnvironment:
    direct_url: str
    management_url: str
    master_key: str
    provider_url: str
    proxy_url: str


def _wait_for_litellm(base_url: str, container: DockerContainer) -> None:
    last_error = 'not attempted'
    for _ in range(180):
        try:
            response = httpx.get(f'{base_url}/health/liveliness', timeout=1)
            if response.is_success:
                return
            last_error = f'{response.status_code}: {response.text}'
        except httpx.HTTPError as error:
            last_error = str(error)
        time.sleep(1)
    logs = container.get_logs()[0].decode(errors='replace')[-8000:]
    raise RuntimeError(f'LiteLLM did not become ready: {last_error}\n{logs}')


def _write_litellm_config(path: Path, provider_port: int) -> None:
    path.write_text(
        f"""model_list:
  - model_name: budget-test-model
    litellm_params:
      model: openai/budget-test-model
      api_base: http://host.docker.internal:{provider_port}/v1
      api_key: test-provider-key
      input_cost_per_token: 0.05
      output_cost_per_token: 0.1

general_settings:
  master_key: {MASTER_KEY}
  proxy_batch_write_at: 1
  user_api_key_cache_ttl: {AUTH_CACHE_TTL}

litellm_settings:
  telemetry: false
  num_retries: 0
"""
    )


@pytest.fixture(scope='session')
def postgres_server(
    pytestconfig: pytest.Config,
) -> postgres_testdb.PostgresServer:
    return postgres_testdb.server_from(pytestconfig)


@pytest.fixture(scope='session')
def postgres_template(postgres_server: postgres_testdb.PostgresServer) -> str:
    return postgres_testdb.TEMPLATE_DB


@pytest.fixture
def test_database(
    postgres_server: postgres_testdb.PostgresServer,
    postgres_template: str,
) -> Iterator[postgres_testdb.TestDatabase]:
    database = postgres_testdb.create_test_database(postgres_server, postgres_template)
    try:
        yield postgres_testdb.TestDatabase(server=postgres_server, name=database)
    finally:
        postgres_testdb.drop_test_database(postgres_server, database)


@pytest.fixture
async def async_engine(
    test_database: postgres_testdb.TestDatabase,
) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(test_database.async_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def async_session_maker(
    async_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_engine, expire_on_commit=False)


@pytest.fixture(scope='session')
def litellm_environment(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[LiteLlmEnvironment]:
    provider_state = ProviderState()
    # Linux containers reach the host gateway, not the host loopback interface.
    with LocalAppServer(
        create_provider_app(provider_state), host='0.0.0.0'
    ) as provider_server:
        config_path = tmp_path_factory.mktemp('litellm') / 'config.yaml'
        _write_litellm_config(config_path, provider_server.port)

        network = Network().create()
        database = (
            PostgresContainer(
                image='postgres:16',
                username='llmproxy',
                password='budget-test-password',
                dbname='litellm',
                driver=None,
            )
            .with_network(network)
            .with_network_aliases('litellm-db')
        )
        litellm = (
            DockerContainer(LITELLM_IMAGE)
            .with_network(network)
            .with_env(
                'DATABASE_URL',
                'postgresql://llmproxy:budget-test-password@litellm-db:5432/litellm',
            )
            .with_env('STORE_MODEL_IN_DB', 'False')
            .with_volume_mapping(config_path, '/app/config.yaml', mode='ro')
            .with_exposed_ports(4000)
            .with_command(['--config', '/app/config.yaml', '--port', '4000'])
        )
        if platform.system() == 'Linux':
            litellm.with_kwargs(extra_hosts={'host.docker.internal': 'host-gateway'})

        try:
            database.start()
            litellm.start()
            direct_url = (
                f'http://{litellm.get_container_host_ip()}:'
                f'{litellm.get_exposed_port(4000)}'
            )
            _wait_for_litellm(direct_url, litellm)

            proxy_state = ProxyState(direct_url)
            with LocalAppServer(create_proxy_app(proxy_state)) as proxy_server:
                headers = {'x-goog-api-key': MASTER_KEY}
                create_response = httpx.post(
                    f'{proxy_server.base_url}/team/new',
                    headers=headers,
                    json={
                        'team_id': BOOTSTRAP_TEAM_ID,
                        'team_alias': 'Budget test bootstrap team',
                        'models': [],
                        'spend': 0,
                    },
                    timeout=30,
                )
                create_response.raise_for_status()
                try:
                    yield LiteLlmEnvironment(
                        direct_url=direct_url,
                        management_url=proxy_server.base_url,
                        master_key=MASTER_KEY,
                        provider_url=provider_server.base_url,
                        proxy_url=proxy_server.base_url,
                    )
                finally:
                    delete_response = httpx.post(
                        f'{proxy_server.base_url}/team/delete',
                        headers=headers,
                        json={'team_ids': [BOOTSTRAP_TEAM_ID]},
                        timeout=30,
                    )
                    delete_response.raise_for_status()
        finally:
            litellm.stop()
            database.stop()
            network.remove()


@pytest.fixture
def configured_litellm_manager(
    litellm_environment: LiteLlmEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> LiteLlmEnvironment:
    import storage.lite_llm_manager as manager_module

    monkeypatch.setattr(
        manager_module, 'LITE_LLM_API_URL', litellm_environment.management_url
    )
    monkeypatch.setattr(
        manager_module, 'LITE_LLM_API_KEY', litellm_environment.master_key
    )
    monkeypatch.setattr(manager_module, 'LITE_LLM_TEAM_ID', BOOTSTRAP_TEAM_ID)

    for url in (
        f'{litellm_environment.provider_url}/test/reset',
        f'{litellm_environment.proxy_url}/test/reset',
    ):
        response = httpx.post(url, timeout=5)
        response.raise_for_status()
    return litellm_environment


@pytest.fixture
async def budget_adapter_factory(
    async_session_maker: async_sessionmaker[AsyncSession],
    configured_litellm_manager: LiteLlmEnvironment,
) -> AsyncIterator[BudgetAdapterFactory]:
    environment = configured_litellm_manager
    factory = BudgetAdapterFactory(
        async_session_maker,
        direct_url=environment.direct_url,
        provider_url=environment.provider_url,
        proxy_url=environment.proxy_url,
    )
    try:
        yield factory
    finally:
        await factory.close()


@pytest.fixture
async def budget_adapter(
    budget_adapter_factory: BudgetAdapterFactory,
) -> BudgetTestAdapter:
    return await budget_adapter_factory.create()


@pytest.fixture
async def budget_http(
    budget_adapter: BudgetTestAdapter,
) -> AsyncIterator[httpx.AsyncClient]:
    from fastapi import FastAPI
    from fastapi.routing import APIRoute

    from server.routes.orgs import _org_budget_service_injector, org_router

    app = FastAPI()
    app.include_router(org_router)

    async def service():
        yield budget_adapter.service
        await budget_adapter.session.commit()

    async def identity():
        return str(budget_adapter.user_ids[0])

    async def context():
        return None

    # Keep real route validation/serialization; authentication is out of scope.
    app.dependency_overrides[_org_budget_service_injector.depends] = service
    for route in app.routes:
        if isinstance(route, APIRoute) and '/budgets' in route.path:
            for dependency in route.dependant.dependencies:
                if dependency.call and dependency.name in {
                    'user_id',
                    'current_user_id',
                }:
                    app.dependency_overrides[dependency.call] = identity
                elif dependency.call and dependency.name is None:
                    app.dependency_overrides[dependency.call] = context
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://budget-test'
    ) as client:
        yield client


@pytest.fixture
async def upgrade_adapter_factory(
    async_session_maker: async_sessionmaker[AsyncSession],
    configured_litellm_manager: LiteLlmEnvironment,
) -> AsyncIterator[UpgradeBudgetAdapterFactory]:
    environment = configured_litellm_manager
    factory = UpgradeBudgetAdapterFactory(
        async_session_maker,
        direct_url=environment.direct_url,
        provider_url=environment.provider_url,
        proxy_url=environment.proxy_url,
    )
    try:
        yield factory
    finally:
        await factory.close()


@pytest.fixture
async def upgrade_adapter(
    upgrade_adapter_factory: UpgradeBudgetAdapterFactory,
) -> UpgradeBudgetTestAdapter:
    return await upgrade_adapter_factory.create()
