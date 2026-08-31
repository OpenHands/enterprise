"""Rewrite saved OpenHands default-model references after default changes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from storage.org import Org
from storage.org_member import OrgMember
from storage.user import User
from storage.user_settings import UserSettings

_OPENHANDS_PREFIX = 'openhands/'
_LITELLM_PROXY_PREFIX = 'litellm_proxy/'


def _build_default_model_replacements(
    old_model_name: str, new_model_name: str
) -> dict[str, str]:
    new_model = f'{_OPENHANDS_PREFIX}{new_model_name}'
    return {
        f'{_OPENHANDS_PREFIX}{old_model_name}': new_model,
        f'{_LITELLM_PROXY_PREFIX}{old_model_name}': new_model,
    }


def replace_model_values(value: Any, replacements: dict[str, str]) -> tuple[Any, bool]:
    """Return a copy with exact managed model-string replacements applied."""
    if isinstance(value, dict):
        changed = False
        replaced = {}
        for key, item in value.items():
            if key == 'model' and isinstance(item, str) and item in replacements:
                replaced[key] = replacements[item]
                changed = True
                continue

            new_item, item_changed = replace_model_values(item, replacements)
            replaced[key] = new_item
            changed = changed or item_changed
        return replaced, changed

    if isinstance(value, list):
        changed = False
        replaced_items = []
        for item in value:
            new_item, item_changed = replace_model_values(item, replacements)
            replaced_items.append(new_item)
            changed = changed or item_changed
        return replaced_items, changed

    return value, False


async def _replace_json_column(
    db_session: AsyncSession,
    model_cls: type,
    column_name: str,
    replacements: dict[str, str],
) -> int:
    result: Any = await db_session.execute(select(model_cls))
    updated = 0
    for row in result.scalars().all():
        current = getattr(row, column_name)
        if current is None:
            continue
        replaced, changed = replace_model_values(deepcopy(current), replacements)
        if changed:
            setattr(row, column_name, replaced)
            updated += 1
    return updated


async def replace_openhands_default_model_references(
    db_session: AsyncSession,
    *,
    old_model_name: str,
    new_model_name: str,
) -> int:
    """Replace current saved references from the old default to the new one.

    Only exact managed model ids are rewritten. BYOK/custom bare models and
    third-party provider-prefixed values are deliberately left untouched.
    """
    if old_model_name == new_model_name:
        return 0

    replacements = _build_default_model_replacements(old_model_name, new_model_name)
    updated = 0
    for model_cls, column_name in (
        (UserSettings, 'agent_settings'),
        (Org, 'agent_settings'),
        (OrgMember, 'agent_settings_diff'),
        (User, 'llm_profiles'),
        (Org, 'llm_profiles'),
    ):
        updated += await _replace_json_column(
            db_session, model_cls, column_name, replacements
        )
    await db_session.flush()
    return updated
