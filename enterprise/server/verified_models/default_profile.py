from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.settings.llm_profiles import LLMProfiles
from openhands.sdk.llm import LLM

from .verified_model_service import StoredVerifiedModel

DEFAULT_LLM_PROFILE_NAME = 'Default'
_OPENHANDS_PROVIDER = 'openhands'


async def get_openhands_default_model_name(db_session: AsyncSession) -> str | None:
    result = await db_session.execute(
        select(StoredVerifiedModel.model_name)
        .where(
            StoredVerifiedModel.provider == _OPENHANDS_PROVIDER,
            StoredVerifiedModel.is_default.is_(True),
            StoredVerifiedModel.is_enabled.is_(True),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


def materialize_default_llm_profile(
    profiles: LLMProfiles, model_name: str | None
) -> LLMProfiles:
    if not model_name:
        return profiles

    model = f'{_OPENHANDS_PROVIDER}/{model_name}'
    existing = profiles.get(DEFAULT_LLM_PROFILE_NAME)
    live_llm: dict[str, Any] = {'model': model, 'base_url': None, 'api_key': None}
    profiles.profiles[DEFAULT_LLM_PROFILE_NAME] = (
        existing.model_copy(update=live_llm)
        if existing is not None
        else LLM(model=model, base_url=None, api_key=None)
    )
    if profiles.active is None:
        profiles.active = DEFAULT_LLM_PROFILE_NAME
    return profiles


def materialize_default_llm_payload(
    payload: dict[str, object] | None, model_name: str | None
) -> dict[str, object] | None:
    if not model_name:
        return payload

    profiles = LLMProfiles.model_validate(payload or {})
    materialize_default_llm_profile(profiles, model_name)
    return profiles.model_dump(mode='json', context={'expose_secrets': True})
