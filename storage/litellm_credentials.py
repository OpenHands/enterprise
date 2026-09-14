"""Persist a known credential before issuing it so retries never mint another."""

import hashlib
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.logger import logger
from storage.budget_control import (
    BudgetWriteDenied,
    budget_request_hash,
    current_budget_control,
)
from storage.litellm_key_policy import key_mutation_scope
from storage.llm_credential_operation import LlmCredentialOperation


def credential_hash(key: str) -> str:
    if len(key) == 64 and all(char in '0123456789abcdef' for char in key):
        return key
    return hashlib.sha256(key.encode()).hexdigest()


@asynccontextmanager
async def credential_revocation_scope(
    client: httpx.AsyncClient, api_url: str, key: str
) -> AsyncIterator[bool]:
    from storage.database import a_session_maker

    key_hash = credential_hash(key)
    async with a_session_maker() as session:
        operation = await session.scalar(
            select(LlmCredentialOperation).where(
                LlmCredentialOperation.key_hash == key_hash
            )
        )
        org_id = operation.org_id if operation is not None else None
    if org_id is None:
        response = await client.get(f'{api_url}/key/info', params={'key': key_hash})
        if response.status_code == 404:
            yield False
            return
        response.raise_for_status()
        body = response.json()
        info = body.get('info') if isinstance(body, dict) else None
        if not isinstance(info, dict) or not info.get('team_id'):
            raise BudgetWriteDenied(
                'Credential revocation requires verified organization ownership'
            )
        org_id = UUID(info['team_id'])

    # Revocation may interrupt adoption, but adoption must never recreate credentials.
    async with key_mutation_scope(str(org_id), allow_pending_budget=True):
        control = current_budget_control(org_id)
        operation = await control.session.scalar(
            select(LlmCredentialOperation).where(
                LlmCredentialOperation.key_hash == key_hash
            )
        )
        response = await client.get(f'{api_url}/key/info', params={'key': key_hash})
        present = response.status_code != 404
        if present:
            response.raise_for_status()
            body = response.json()
            info = body.get('info') if isinstance(body, dict) else None
            if (
                not isinstance(info, dict)
                or info.get('team_id') != str(org_id)
                or (operation is not None and info.get('user_id') != operation.user_id)
            ):
                raise BudgetWriteDenied(
                    'Credential ownership changed before revocation'
                )
        if operation is not None:
            operation.status = 'revoked'
            await control.session.commit()
        yield present
        if present:
            response = await client.get(f'{api_url}/key/info', params={'key': key_hash})
            if response.status_code != 404:
                response.raise_for_status()
                raise BudgetWriteDenied('Credential revocation is pending verification')


async def issue_credential(
    client: httpx.AsyncClient,
    api_url: str,
    payload: dict[str, Any],
    check_creation: Callable[[], Awaitable[None]],
    *,
    replacing_key: str | None = None,
) -> str:
    from storage.lite_llm_manager import LiteLlmManager

    org_id = UUID(payload['team_id'])
    control = current_budget_control(org_id)
    request_hash = budget_request_hash(payload)
    operation = await control.session.scalar(
        select(LlmCredentialOperation).where(
            LlmCredentialOperation.org_id == org_id,
            LlmCredentialOperation.request_hash == request_hash,
        )
    )
    if operation is not None:
        if operation.status == 'revoked':
            raise BudgetWriteDenied(
                'This credential was revoked; it cannot be recreated'
            )
        key = operation.payload['key']
        keys = await LiteLlmManager._get_all_keys_for_user(client, payload['user_id'])
        if keys is None:
            raise BudgetWriteDenied(
                'Cannot recover a credential while policy is unavailable'
            )
        if LiteLlmManager._key_belongs_to_user_org(
            keys, key, payload['user_id'], str(org_id), False
        ):
            operation.status = 'issued'
            await control.session.commit()
            return key
        if operation.status == 'issued':
            raise BudgetWriteDenied(
                'The issued credential is missing; it was not recreated'
            )

    # Recheck on retry: an operator may have added restrictions after the timeout.
    await check_creation()
    if operation is None:
        key = f'sk-{secrets.token_hex(32)}'
        operation = LlmCredentialOperation(
            org_id=org_id,
            user_id=payload['user_id'],
            request_hash=request_hash,
            key_hash=hashlib.sha256(key.encode()).hexdigest(),
            payload={
                **payload,
                'key': key,
                **({'_retire_key': replacing_key} if replacing_key else {}),
            },
            replaces_key_hash=credential_hash(replacing_key) if replacing_key else None,
        )
        control.session.add(operation)
        await control.session.commit()

    control.assert_locked()
    response = await client.post(
        f'{api_url}/key/generate',
        json={
            name: value
            for name, value in operation.payload.items()
            if name != '_retire_key'
        },
    )
    response.raise_for_status()
    key = operation.payload['key']
    keys = await LiteLlmManager._get_all_keys_for_user(client, payload['user_id'])
    if keys is None or not LiteLlmManager._key_belongs_to_user_org(
        keys, key, payload['user_id'], str(org_id), False
    ):
        raise BudgetWriteDenied(
            'Credential issuance is pending verification; retry safely'
        )
    operation.status = 'issued'
    await control.session.commit()
    return key


async def activate_credential(
    session: AsyncSession, org_id: UUID, user_id: str, key: str
) -> None:
    """Record activation in the same transaction as the member's credential pointer."""
    current_budget_control(org_id).assert_locked()
    operation = await session.scalar(
        select(LlmCredentialOperation).where(
            LlmCredentialOperation.org_id == org_id,
            LlmCredentialOperation.user_id == user_id,
            LlmCredentialOperation.key_hash == credential_hash(key),
        )
    )
    if operation is None or operation.status != 'issued':
        raise BudgetWriteDenied('Credential activation requires verified issuance')
    if operation.activated_at is None:
        operation.activated_at = datetime.now(UTC)


async def retire_replaced_credentials(org_id: UUID) -> dict[str, int]:
    from storage.lite_llm_manager import LiteLlmManager

    retired = errors = 0
    async with key_mutation_scope(str(org_id), allow_pending_budget=True):
        control = current_budget_control(org_id)
        operation_ids = list(
            (
                await control.session.scalars(
                    select(LlmCredentialOperation.id)
                    .where(
                        LlmCredentialOperation.org_id == org_id,
                        LlmCredentialOperation.replaces_key_hash.is_not(None),
                        LlmCredentialOperation.activated_at.is_not(None),
                        LlmCredentialOperation.retired_at.is_(None),
                    )
                    .order_by(LlmCredentialOperation.created_at)
                    .limit(25)
                )
            ).all()
        )
        for operation_id in operation_ids:
            try:
                operation = await control.session.get(
                    LlmCredentialOperation, operation_id
                )
                if operation is None or operation.retired_at is not None:
                    continue
                old_key = operation.payload['_retire_key']
                if credential_hash(old_key) != operation.replaces_key_hash:
                    raise BudgetWriteDenied('Credential retirement identity is invalid')
                await LiteLlmManager.delete_key(old_key)
                operation.retired_at = datetime.now(UTC)
                await control.session.commit()
                retired += 1
            except Exception as exc:
                await control.session.rollback()
                logger.warning(
                    'credential_retirement_failed',
                    extra={
                        'org_id': str(org_id),
                        'operation_id': str(operation_id),
                        'error_type': type(exc).__name__,
                    },
                )
                errors += 1
    return {'retired': retired, 'error_count': errors}
