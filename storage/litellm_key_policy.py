"""Prevent credential creation from bypassing an existing key's policy."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from storage.budget_control import (
    BudgetControlConflict,
    BudgetControlSession,
    BudgetWriteDenied,
    budget_control_session,
    budget_engine,
)

_OBSERVATION_FIELDS = {
    'token',
    'key_name',
    'key_alias',
    'user_id',
    'team_id',
    'team_alias',
    'spend',
    'model_spend',
    'created_at',
    'updated_at',
    'created_by',
    'updated_by',
    'last_active',
    'last_used_at',
    'rotation_count',
    'last_rotation_at',
}


def key_restrictions(key: dict[str, Any]) -> list[str]:
    restrictions = []
    for name, value in key.items():
        if name in _OBSERVATION_FIELDS:
            continue
        if name == 'key_type' and value == 'default':
            continue
        if name == 'metadata':
            if value is not None and (
                not isinstance(value, dict) or set(value) - {'type'}
            ):
                restrictions.append(name)
            continue
        if value is False and name in {
            'blocked',
            'auto_rotate',
            'soft_budget_cooldown',
        }:
            continue
        if value is None or value == [] or value == {}:
            continue
        restrictions.append(name)
    return sorted(restrictions)


@asynccontextmanager
async def key_mutation_scope(
    team_id: str | None, *, allow_pending_budget: bool = False
) -> AsyncIterator[BudgetControlSession]:
    from storage.database import a_session_maker

    if team_id is None:
        raise BudgetWriteDenied('Managed credentials require an organization')
    org_id = UUID(team_id)
    async with a_session_maker() as session:
        engine = budget_engine(session)
    async with budget_control_session(engine, org_id) as control:
        if not allow_pending_budget and await control.pending_operation() is not None:
            raise BudgetControlConflict(
                'Finish the pending budget operation before changing keys'
            )
        yield control
