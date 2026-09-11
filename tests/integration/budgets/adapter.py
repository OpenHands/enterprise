from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.routes.org_models import OrgBudgetSettingsUpdate
from server.services.org_budget_service import OrgBudgetService
from storage.lite_llm_manager import LiteLlmManager
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User

FIXED_REQUEST_COST = 1.0


@dataclass
class BudgetTestAdapter:
    session: AsyncSession
    service: OrgBudgetService
    org_id: UUID
    user_ids: tuple[UUID, UUID]
    keys: dict[UUID, str]
    direct_url: str
    provider_url: str
    proxy_url: str

    async def update_settings(self, **changes: Any) -> dict[str, Any]:
        result = await self.service.update_budget_settings(
            self.org_id, OrgBudgetSettingsUpdate(**changes)
        )
        await self.session.commit()
        return result

    async def configure_budget(
        self, organization_limit: float, default_user_limit: float
    ) -> dict[str, Any]:
        return await self.update_settings(
            enabled=True,
            monthly_limit=organization_limit,
            default_user_monthly_limit=default_user_limit,
        )

    async def disable_budget(self) -> dict[str, Any]:
        return await self.update_settings(enabled=False)

    async def set_organization_limit(self, limit: float) -> dict[str, Any]:
        return await self.update_settings(monthly_limit=limit)

    async def set_default_user_limit(self, limit: float) -> dict[str, Any]:
        return await self.update_settings(default_user_monthly_limit=limit)

    async def set_override(
        self, user_id: UUID, limit: float | None, disabled: bool = False
    ) -> None:
        await self.service.upsert_user_override(
            self.org_id,
            user_id,
            monthly_limit=limit,
            is_disabled=disabled,
        )
        await self.session.commit()

    async def delete_override(self, user_id: UUID) -> None:
        await self.service.delete_user_override(self.org_id, user_id)
        await self.session.commit()

    async def run_maintenance(self) -> dict[str, Any]:
        result = await self.service.run_budget_maintenance(self.org_id)
        await self.session.commit()
        return result

    async def budget_state(self) -> dict[str, Any]:
        result = await self.service.get_budget_state(self.org_id)
        await self.session.commit()
        return result

    async def financial_data(self) -> dict[str, Any]:
        return await LiteLlmManager.get_team_members_financial_data(str(self.org_id))

    async def wait_for_spend(self, expected_team_spend: float) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            financial_data = await self.financial_data()
            if financial_data['team_spend'] == expected_team_spend:
                return financial_data
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError(
                    f'expected team spend {expected_team_spend}, got '
                    f'{financial_data["team_spend"]}'
                )
            await asyncio.sleep(0.1)

    async def fail_next_management_call(
        self, path: str, status_code: int = 500, count: int = 1
    ) -> None:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f'{self.proxy_url}/test/fail-next',
                json={
                    'path': path,
                    'status_code': status_code,
                    'remaining': count,
                },
                timeout=5,
            )
        response.raise_for_status()

    async def reset_faults(self) -> None:
        async with httpx.AsyncClient() as client:
            response = await client.post(f'{self.proxy_url}/test/reset', timeout=5)
        response.raise_for_status()

    async def send_request(self, user_id: UUID) -> httpx.Response:
        async with httpx.AsyncClient() as client:
            return await client.post(
                f'{self.direct_url}/v1/chat/completions',
                headers={'Authorization': f'Bearer {self.keys[user_id]}'},
                json={
                    'model': 'budget-test-model',
                    'messages': [{'role': 'user', 'content': 'charge one unit'}],
                },
                timeout=30,
            )

    async def send_concurrent_requests(
        self, user_ids: list[UUID]
    ) -> list[httpx.Response]:
        return await asyncio.gather(
            *(self.send_request(user_id) for user_id in user_ids)
        )

    async def provider_calls(self) -> int:
        async with httpx.AsyncClient() as client:
            response = await client.get(f'{self.provider_url}/test/requests', timeout=5)
        response.raise_for_status()
        return int(response.json()['calls'])


@dataclass
class _ProvisionedBudgetScenario:
    adapter: BudgetTestAdapter
    team_created: bool = False


class BudgetAdapterFactory:
    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        *,
        direct_url: str,
        provider_url: str,
        proxy_url: str,
    ):
        self.session_maker = session_maker
        self.direct_url = direct_url
        self.provider_url = provider_url
        self.proxy_url = proxy_url
        self._scenarios: list[_ProvisionedBudgetScenario] = []

    async def create(self) -> BudgetTestAdapter:
        session = self.session_maker()
        org = Org(name=f'Budget property test {time.time_ns()}')
        role = Role(name=f'budget-member-{time.time_ns()}', rank=1)
        session.add_all([org, role])
        await session.flush()

        users = (
            User(id=uuid4(), current_org_id=org.id),
            User(id=uuid4(), current_org_id=org.id),
        )
        session.add_all(users)
        await session.flush()
        session.add_all(
            [
                OrgMember(
                    org_id=org.id,
                    user_id=user.id,
                    role_id=role.id,
                    _llm_api_key='unused-by-budget-tests',
                    status='active',
                )
                for user in users
            ]
        )
        await session.commit()

        adapter = BudgetTestAdapter(
            session=session,
            service=OrgBudgetService(db_session=session),
            org_id=org.id,
            user_ids=(users[0].id, users[1].id),
            keys={},
            direct_url=self.direct_url,
            provider_url=self.provider_url,
            proxy_url=self.proxy_url,
        )
        scenario = _ProvisionedBudgetScenario(adapter=adapter)
        self._scenarios.append(scenario)

        try:
            await LiteLlmManager.create_team(
                team_alias=org.name,
                team_id=str(org.id),
                max_budget=100.0,
            )
            scenario.team_created = True
            for user in users:
                created = await LiteLlmManager.create_user(
                    email=f'{user.id}@example.com',
                    keycloak_user_id=str(user.id),
                )
                if not created:
                    raise RuntimeError(f'failed to provision LiteLLM user {user.id}')
                await LiteLlmManager.add_user_to_team(
                    keycloak_user_id=str(user.id),
                    team_id=str(org.id),
                    max_budget=100.0,
                )
                adapter.keys[user.id] = await LiteLlmManager.generate_key(
                    keycloak_user_id=str(user.id),
                    team_id=str(org.id),
                    key_alias=f'budget-test-{user.id}',
                    metadata={'budget_property_test': True},
                )
        except Exception:
            await self._cleanup(scenario)
            self._scenarios.remove(scenario)
            raise

        return adapter

    async def close(self) -> None:
        for scenario in reversed(self._scenarios):
            await self._cleanup(scenario)
        self._scenarios.clear()

    async def _cleanup(self, scenario: _ProvisionedBudgetScenario) -> None:
        adapter = scenario.adapter
        for key in adapter.keys.values():
            with suppress(Exception):
                await LiteLlmManager.delete_key(key)
        for user_id in adapter.user_ids:
            with suppress(Exception):
                await LiteLlmManager.delete_user(str(user_id))
        if scenario.team_created:
            with suppress(Exception):
                await LiteLlmManager.delete_team(str(adapter.org_id))
        await adapter.session.close()
