"""Idempotent LiteLLM team/user/member-key provisioning for native accounts."""

import hashlib
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from server.constants import (
    LITE_LLM_API_KEY,
    LITE_LLM_API_URL,
    should_use_direct_llm_defaults,
)
from server.services.native_provisioning_service import (
    NativeProvisionedKeys,
    NativeProvisioningRequest,
)
from storage.lite_llm_manager import (
    LITELLM_MANAGEMENT_TIMEOUT,
    LiteLlmManager,
    _get_default_initial_budget,
    _is_billing_enabled,
    get_openhands_cloud_key_alias,
)
from storage.native_external_work import NativeExternalPayload


def _client() -> httpx.AsyncClient:
    if not LITE_LLM_API_KEY or not LITE_LLM_API_URL:
        raise RuntimeError('LiteLLM management is not configured')
    return httpx.AsyncClient(
        headers={'x-goog-api-key': LITE_LLM_API_KEY},
        timeout=httpx.Timeout(LITELLM_MANAGEMENT_TIMEOUT),
    )


class NativeTeamInfo(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    max_budget: float | None = None


class NativeTeamEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    team_info: NativeTeamInfo = Field(default_factory=NativeTeamInfo)


class NativeKeyMetadata(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    native_work_id: str | None = None


class NativeKeyInfo(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    user_id: str | None = None
    team_id: str | None = None
    metadata: NativeKeyMetadata | None = None


class NativeKeyInfoEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    info: NativeKeyInfo | None = None


async def _key_info(client: httpx.AsyncClient, key: str) -> NativeKeyInfo | None:
    response = await client.get(
        f'{LITE_LLM_API_URL}/key/info',
        params={'key': hashlib.sha256(key.encode()).hexdigest()},
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return NativeKeyInfoEnvelope.model_validate_json(response.content).info


async def provision_native_member(
    request: NativeProvisioningRequest,
) -> NativeProvisionedKeys:
    user_id, org_id = str(request.account_id), str(request.org_id)
    key = request.member_key.get_secret_value()
    async with _client() as client:
        try:
            payload = await LiteLlmManager._get_team(client, org_id)
            team = (
                NativeTeamEnvelope.model_validate(payload)
                if payload is not None
                else None
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            team = None
        budget = _get_default_initial_budget(await _is_billing_enabled())
        if team is None:
            await LiteLlmManager._create_team(
                client,
                await LiteLlmManager._team_alias_for_org(org_id, user_id),
                org_id,
                budget,
            )
        else:
            budget = team.team_info.max_budget
        if not await LiteLlmManager._create_user(client, request.email, user_id):
            raise RuntimeError('LiteLLM user provisioning failed')
        await LiteLlmManager._add_user_to_team(client, user_id, org_id, budget)
        info = await _key_info(client, key)
        if info is None:
            response = await client.post(
                f'{LITE_LLM_API_URL}/key/generate',
                json={
                    'key': key,
                    'user_id': user_id,
                    'team_id': org_id,
                    'key_alias': f'{get_openhands_cloud_key_alias(user_id, org_id)}:{request.work_id}',
                    'models': [],
                    'metadata': {
                        'type': 'openhands',
                        'native_work_id': request.idempotency_key,
                    },
                },
            )
            # A response can be lost after commit. The persisted random key is
            # reused and its full owner/team metadata is verified on every retry.
            if not response.is_success and response.status_code not in (400, 409):
                response.raise_for_status()
            info = await _key_info(client, key)
        if (
            not info
            or info.user_id != user_id
            or info.team_id != org_id
            or (info.metadata.native_work_id if info.metadata else None)
            != request.idempotency_key
        ):
            raise RuntimeError('LiteLLM key ownership verification failed')
    return NativeProvisionedKeys(member_key=request.member_key)


async def cleanup_native_resource(
    kind: str,
    account_id: UUID | None,
    org_id: UUID | None,
    payload: NativeExternalPayload,
) -> None:
    if should_use_direct_llm_defaults() and not LITE_LLM_API_URL:
        return
    async with _client() as client:
        if kind == 'provision':
            key = payload.get('member_key')
            if not key or await _key_info(client, key) is None:
                return
            response = await client.post(
                f'{LITE_LLM_API_URL}/key/delete', json={'keys': [key]}
            )
        elif kind == 'delete_user':
            response = await client.post(
                f'{LITE_LLM_API_URL}/user/delete', json={'user_ids': [str(account_id)]}
            )
        elif kind == 'delete_team':
            response = await client.post(
                f'{LITE_LLM_API_URL}/team/delete', json={'team_ids': [str(org_id)]}
            )
        else:
            raise ValueError('Unknown native external cleanup operation')
        if response.status_code != 404:
            response.raise_for_status()
