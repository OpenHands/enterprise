from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.settings.llm_profiles import LLMProfiles
from openhands.app_server.utils.litellm_integration import (
    is_litellm_enabled,
    is_managed_llm,
)
from openhands.app_server.utils.llm import is_openhands_model
from openhands.sdk.llm import LLM

from .verified_model_service import StoredVerifiedModel

DEFAULT_LLM_PROFILE_NAME = 'Default'
_OPENHANDS_PROVIDER = 'openhands'


async def get_openhands_default_model_name(db_session: AsyncSession) -> str | None:
    if not is_litellm_enabled():
        return None
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
    if not is_litellm_enabled():
        existing = profiles.get(DEFAULT_LLM_PROFILE_NAME)
        if existing is not None and is_managed_llm(existing.model, existing.base_url):
            profiles.profiles.pop(DEFAULT_LLM_PROFILE_NAME, None)
        if profiles.active is not None:
            active = profiles.get(profiles.active)
            if active is None or is_managed_llm(active.model, active.base_url):
                profiles.active = None
        return profiles
    if not model_name:
        # No enabled OpenHands DB default. The logical ``Default`` profile is a
        # live pointer to the managed OpenHands default, so when it currently
        # holds an OpenHands-managed model whose DB row was disabled/deleted it
        # must not linger — otherwise list_profiles / get_profile /
        # SaasSettingsStore.load() keep exposing and launching the stale model,
        # violating the contract that a disabled default is ignored
        # immediately. A non-OpenHands ``Default`` (e.g. a legacy seeded
        # concrete LLM) is a user-owned concrete profile, not a managed-default
        # pointer, so it is preserved.
        existing = profiles.get(DEFAULT_LLM_PROFILE_NAME)
        if existing is not None and is_openhands_model(existing.model):
            profiles.profiles.pop(DEFAULT_LLM_PROFILE_NAME, None)
            # The logical pointer is gone; ``active`` must not keep pointing
            # at a profile that no longer exists (a downstream launch would
            # otherwise resolve the stale stored snapshot).
            if profiles.active == DEFAULT_LLM_PROFILE_NAME:
                profiles.active = None
        return profiles

    existing = profiles.get(DEFAULT_LLM_PROFILE_NAME)
    if existing is not None and not is_managed_llm(existing.model, existing.base_url):
        # A concrete native Default may have been seeded while the gateway was
        # disabled. Reenabling a DB default does not
        # turn that profile into a live managed pointer.
        return profiles
    model = f'{_OPENHANDS_PROVIDER}/{model_name}'
    profiles.profiles[DEFAULT_LLM_PROFILE_NAME] = (
        existing.model_copy(update={'model': model, 'base_url': None, 'api_key': None})
        if existing is not None
        else LLM(model=model, base_url=None, api_key=None)
    )
    if profiles.active is None:
        profiles.active = DEFAULT_LLM_PROFILE_NAME
    return profiles


def materialize_default_llm_payload(
    payload: dict[str, object] | None, model_name: str | None
) -> dict[str, object] | None:
    profiles = LLMProfiles.model_validate(payload or {})
    materialize_default_llm_profile(profiles, model_name)
    return profiles.model_dump(mode='json', context={'expose_secrets': True})
