"""
Store class for managing organizational settings.
"""

import functools
import hashlib
import hmac
import math
import os
from typing import Any, Awaitable, Callable
from uuid import UUID

import httpx
from pydantic import SecretStr

from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.utils.http_session import httpx_verify_option
from server.auth.token_manager import TokenManager
from server.constants import (
    LITE_LLM_API_KEY,
    LITE_LLM_API_URL,
    LITE_LLM_TEAM_ID,
    ORG_SETTINGS_VERSION,
    get_default_litellm_model,
    get_default_llm_api_key,
    get_default_llm_base_url,
    get_default_llm_model,
    should_use_direct_llm_defaults,
)
from server.logger import logger
from storage.litellm_key_policy import key_mutation_scope, key_restrictions
from storage.user_settings import UserSettings

# Timeout in seconds for LiteLLM management API requests.
LITELLM_MANAGEMENT_TIMEOUT = float(os.getenv('LITELLM_MANAGEMENT_TIMEOUT', '5'))

# Timeout in seconds for key verification requests to LiteLLM
KEY_VERIFICATION_TIMEOUT = 5.0

# A very large number to represent "unlimited" until LiteLLM fixes their unlimited update bug.
UNLIMITED_BUDGET_SETTING = 1000000000.0


# Import-time snapshot of the ENABLE_BILLING env var. Runtime checks prefer
# ``_is_billing_enabled`` (service ``resolve``), so a DB-managed ENABLE_BILLING
# flag row wins at runtime; this snapshot is only the emergency fallback when
# the service cannot even be imported (OSS installs without the enterprise
# package path) or evaluation fails.
ENABLE_BILLING = os.environ.get('ENABLE_BILLING', 'false').lower() in ('true', '1')


async def _is_billing_enabled() -> bool:
    """Resolve the ENABLE_BILLING default flag at runtime.

    Goes through the feature flag service's fault-tolerant ``resolve``: a
    database row wins, the registered env-var default is the fallback, and a
    failed evaluation falls back to the same default. Import is lazy to avoid
    a ``storage``-package import cycle; on an import failure the import-time
    env snapshot is used.
    """
    try:
        from server.services.feature_flag_service import feature_flag_service

        return await feature_flag_service.resolve('ENABLE_BILLING')
    except ImportError:
        return ENABLE_BILLING


def _get_default_initial_budget(billing_enabled: bool) -> float | None:
    """Get the default initial budget for new teams.

    When billing is disabled (the ENABLE_BILLING feature flag is off), returns
    None to disable budget enforcement in LiteLLM. When billing is enabled,
    returns the DEFAULT_INITIAL_BUDGET environment variable value (default 0.0).

    Returns:
        float | None: The default budget, or None to disable budget enforcement.
    """
    if not billing_enabled:
        return None

    try:
        budget = float(os.environ.get('DEFAULT_INITIAL_BUDGET', 0.0))
        if budget < 0:
            raise ValueError(
                f'DEFAULT_INITIAL_BUDGET must be non-negative, got {budget}'
            )
        return budget
    except ValueError as e:
        raise ValueError(
            f'Invalid DEFAULT_INITIAL_BUDGET environment variable: {e}'
        ) from e


DEFAULT_INITIAL_BUDGET: float | None = _get_default_initial_budget(ENABLE_BILLING)


# Fallback models offered at $0 cost in the LiteLLM proxy. The DB-backed
# verified_models.is_free flag is the runtime source of truth; this env-backed
# list only covers bootstrap/fallback paths where the DB cannot be queried.
_DEFAULT_FREE_LLM_MODELS = [
    'glm-5.2',
    'minimax-m2.5',
    'minimax-m2.7',
    'kimi-k3',
    'deepseek-v4-flash',
]
_NO_FREE_LLM_MODELS = ['__openhands_no_free_models__']


def _get_free_llm_models() -> list[str]:
    raw = os.environ.get('FREE_LLM_MODELS')
    if not raw:
        return list(_DEFAULT_FREE_LLM_MODELS)
    models = [m.strip() for m in raw.split(',') if m.strip()]
    return models or list(_DEFAULT_FREE_LLM_MODELS)


FREE_LLM_MODELS: list[str] = _get_free_llm_models()


def _litellm_free_model_allowlist(free_models: list[str]) -> list[str]:
    return list(free_models) if free_models else list(_NO_FREE_LLM_MODELS)


def _normalize_litellm_model_name(model_name: str) -> str:
    for prefix in ('openhands/', 'litellm_proxy/'):
        if model_name.startswith(prefix):
            return model_name.removeprefix(prefix)
    return model_name


def _is_free_budget(max_budget: float | None) -> bool:
    """A team is on the free tier when it has a non-null budget of zero.

    ``None`` means budget enforcement is already disabled (billing off or an
    unlimited team) and is left untouched; only an explicit ``0.0`` budget
    triggers the free-model restriction.
    """
    return max_budget is not None and max_budget <= 0.0


def _validated_budget_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f'LiteLLM {field} must be a finite non-negative number')
    return float(value)


def get_openhands_cloud_key_alias(keycloak_user_id: str, org_id: str) -> str:
    """Generate the key alias for OpenHands Cloud managed keys."""
    return f'OpenHands Cloud - user {keycloak_user_id} - org {org_id}'


def get_byor_key_alias(keycloak_user_id: str, org_id: str) -> str:
    """Generate the key alias for BYOR (Bring Your Own Runtime) keys."""
    return f'BYOR Key - user {keycloak_user_id}, org {org_id}'


def get_org_team_alias(org_id: str, org_name: str | None, user_id: str | None) -> str:
    """Human-readable LiteLLM team_alias for an org's team.

    Personal orgs (org_id == user_id) get "Personal Workspace"; team orgs use
    their display name. Falls back to the id when no name is available, never
    the bare user uid (which made teams indistinguishable in the dashboard).
    """
    if str(org_id) == str(user_id):
        return 'Personal Workspace'
    return org_name or f'Organization {org_id}'


class LiteLlmManager:
    """Manage LiteLLM interactions."""

    @staticmethod
    async def _apply_budget_write(
        client: httpx.AsyncClient,
        org_id: UUID,
        operation_id: UUID,
        path: str,
        body: dict[str, Any],
    ) -> None:
        from storage.budget_control import current_budget_control

        if not LITE_LLM_API_KEY or not LITE_LLM_API_URL:
            raise RuntimeError('LiteLLM management API is not configured')
        await current_budget_control(org_id).authorize_write(operation_id, path, body)
        response = await client.post(f'{LITE_LLM_API_URL}{path}', json=body)
        response.raise_for_status()

    @staticmethod
    def get_budget_from_team_info(
        user_team_info: dict | None, user_id: str, org_id: str
    ) -> tuple[float | None, float] | None:
        """Extract known budget data, preserving an explicit unlimited cap."""
        if not user_team_info or 'spend' not in user_team_info:
            return None

        spend = user_team_info['spend'] or 0
        if user_id == org_id:
            if 'litellm_budget_table' not in user_team_info:
                return None
            budget_table = user_team_info['litellm_budget_table']
            max_budget = (
                budget_table.get('max_budget') if budget_table is not None else None
            )
        else:
            if 'max_budget_in_team' not in user_team_info:
                return None
            max_budget = user_team_info['max_budget_in_team']

        return max_budget, spend

    @staticmethod
    async def _get_db_free_llm_models(db_session) -> list[str]:
        from sqlalchemy import and_, select

        from server.verified_models.verified_model_service import StoredVerifiedModel

        result = await db_session.execute(
            select(StoredVerifiedModel.model_name)
            .where(
                and_(
                    StoredVerifiedModel.provider == 'openhands',
                    StoredVerifiedModel.is_enabled.is_(True),
                    StoredVerifiedModel.is_free.is_(True),
                )
            )
            .order_by(StoredVerifiedModel.model_name)
        )
        return [
            _normalize_litellm_model_name(model_name)
            for model_name in result.scalars().all()
        ]

    @staticmethod
    async def _resolve_free_llm_models(db_session=None) -> list[str]:
        if db_session is not None:
            return await LiteLlmManager._get_db_free_llm_models(db_session)

        try:
            from storage.database import a_session_maker

            async with a_session_maker() as session:
                return await LiteLlmManager._get_db_free_llm_models(session)
        except Exception:
            logger.warning('Falling back to env FREE_LLM_MODELS for LiteLLM allowlist')
            return list(FREE_LLM_MODELS)

    @staticmethod
    def _is_free_team_model_allowlist(
        models: list[str],
        free_models: list[str],
        previous_free_models: list[str] | None = None,
    ) -> bool:
        if not models:
            return False

        model_set = {_normalize_litellm_model_name(model) for model in models}
        candidate_sets = [free_models, FREE_LLM_MODELS, _NO_FREE_LLM_MODELS]
        if previous_free_models is not None:
            candidate_sets.append(previous_free_models)

        return any(
            model_set.issubset(
                {_normalize_litellm_model_name(model) for model in candidate_set}
            )
            for candidate_set in candidate_sets
            if candidate_set
        )

    @staticmethod
    def _is_free_tier_team_info(
        team_info: dict,
        free_models: list[str],
        previous_free_models: list[str] | None = None,
    ) -> bool:
        max_budget = team_info.get('max_budget')
        if _is_free_budget(max_budget):
            return True
        if max_budget is not None:
            return False
        return LiteLlmManager._is_free_team_model_allowlist(
            team_info.get('models') or [],
            free_models,
            previous_free_models,
        )

    @staticmethod
    async def _get_org_ids(db_session) -> list[str]:
        from sqlalchemy import select

        from storage.org import Org

        result = await db_session.execute(select(Org.id))
        return [str(org_id) for org_id in result.scalars().all()]

    @staticmethod
    async def sync_free_model_allowlists(
        db_session, previous_free_models: list[str] | None = None
    ) -> None:
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return

        free_models = await LiteLlmManager._resolve_free_llm_models(db_session)
        org_ids = await LiteLlmManager._get_org_ids(db_session)

        async with httpx.AsyncClient(
            headers={'x-goog-api-key': LITE_LLM_API_KEY},
            timeout=httpx.Timeout(LITELLM_MANAGEMENT_TIMEOUT),
        ) as client:
            for org_id in org_ids:
                try:
                    team = await LiteLlmManager._get_team(client, org_id)
                    if not team:
                        continue
                    team_info = team.get('team_info', {})
                    if LiteLlmManager._is_free_tier_team_info(
                        team_info, free_models, previous_free_models
                    ):
                        await LiteLlmManager._update_team(
                            client,
                            org_id,
                            None,
                            0.0,
                            free_models=free_models,
                        )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 404:
                        logger.warning(
                            'Failed to sync LiteLLM free-model allowlist',
                            extra={'org_id': org_id},
                        )
                except Exception:
                    logger.warning(
                        'Failed to sync LiteLLM free-model allowlist',
                        extra={'org_id': org_id},
                    )

    @staticmethod
    async def create_entries(
        org_id: str,
        keycloak_user_id: str,
        oss_settings: Settings,
        create_user: bool,
        add_user_to_team: bool = True,
    ) -> Settings | None:
        logger.info(
            'SettingsStore:update_settings_with_litellm_default:start',
            extra={'org_id': org_id, 'user_id': keycloak_user_id},
        )
        if should_use_direct_llm_defaults():
            llm_settings: dict[str, Any] = {
                'model': get_default_llm_model(),
                'base_url': get_default_llm_base_url(),
            }
            default_api_key = get_default_llm_api_key()
            if default_api_key:
                llm_settings['api_key'] = default_api_key
            oss_settings.update(
                {
                    'agent_settings_diff': {
                        'agent': 'CodeActAgent',
                        'llm': llm_settings,
                    }
                }
            )
            return oss_settings

        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return None
        local_deploy = os.environ.get('LOCAL_DEPLOYMENT', None)
        key = LITE_LLM_API_KEY
        if not local_deploy:
            token_manager = TokenManager()
            keycloak_user_info = (
                await token_manager.get_user_info_from_user_id(keycloak_user_id) or {}
            )

            async with httpx.AsyncClient(
                headers={
                    'x-goog-api-key': LITE_LLM_API_KEY,
                },
                timeout=httpx.Timeout(LITELLM_MANAGEMENT_TIMEOUT),
            ) as client:
                free_llm_models = await LiteLlmManager._resolve_free_llm_models()

                # Check if team already exists and get its budget
                # New users joining existing orgs should inherit the team's budget
                # When billing is disabled, the default budget is None (no
                # budget enforcement)
                team_budget: float | None = _get_default_initial_budget(
                    await _is_billing_enabled()
                )
                try:
                    existing_team = await LiteLlmManager._get_team(client, org_id)
                    if existing_team:
                        team_info = existing_team.get('team_info', {})
                        # Preserve None from existing team (no budget enforcement)
                        existing_budget = team_info.get('max_budget')
                        # A free-tier team has its model list restricted to
                        # the DB-backed free model set with max_budget cleared
                        # (None). Re-deriving the budget as None would otherwise
                        # drop the restriction on the next provisioning, so
                        # recognize that allowlist and restore budget 0.0.
                        existing_models = team_info.get('models') or []
                        if (
                            existing_budget is None
                            and existing_models
                            and LiteLlmManager._is_free_team_model_allowlist(
                                existing_models, free_llm_models
                            )
                        ):
                            existing_budget = 0.0
                        team_budget = existing_budget
                        logger.info(
                            'LiteLlmManager:create_entries:existing_team_budget',
                            extra={
                                'org_id': org_id,
                                'user_id': keycloak_user_id,
                                'team_budget': team_budget,
                            },
                        )
                except httpx.HTTPStatusError as e:
                    # Team doesn't exist yet (404) - this is expected for first user
                    if e.response.status_code != 404:
                        raise
                    logger.info(
                        'LiteLlmManager:create_entries:no_existing_team',
                        extra={'org_id': org_id, 'user_id': keycloak_user_id},
                    )

                team_alias = await LiteLlmManager._team_alias_for_org(
                    org_id, keycloak_user_id
                )
                await LiteLlmManager._create_team(
                    client,
                    team_alias,
                    org_id,
                    team_budget,
                    free_models=free_llm_models,
                )

                if add_user_to_team:
                    if create_user:
                        # A missing OpenHands row does not prove LiteLLM state is disposable.
                        user_created = await LiteLlmManager._create_user(
                            client, keycloak_user_info.get('email'), keycloak_user_id
                        )
                        if not user_created:
                            logger.error(
                                'create_entries_failed_user_creation',
                                extra={
                                    'org_id': org_id,
                                    'user_id': keycloak_user_id,
                                },
                            )
                            return None

                    # Verify user exists before proceeding with key generation
                    user_exists = await LiteLlmManager._user_exists(
                        client, keycloak_user_id
                    )
                    if not user_exists:
                        logger.error(
                            'create_entries_user_not_found_before_key_generation',
                            extra={
                                'org_id': org_id,
                                'user_id': keycloak_user_id,
                                'create_user_flag': create_user,
                            },
                        )
                        return None

                    await LiteLlmManager._add_user_to_team(
                        client, keycloak_user_id, org_id, team_budget
                    )

                    key_alias = get_openhands_cloud_key_alias(keycloak_user_id, org_id)
                    key = await LiteLlmManager._generate_key(
                        client,
                        keycloak_user_id,
                        org_id,
                        key_alias,
                        None,
                    )
                else:
                    key = ''

        oss_settings.update(
            {
                'agent_settings_diff': {
                    'agent': 'CodeActAgent',
                    'llm': {
                        'model': get_default_litellm_model(),
                        'api_key': key,
                        'base_url': LITE_LLM_API_URL,
                    },
                }
            }
        )
        return oss_settings

    @staticmethod
    async def migrate_entries(
        org_id: str,
        keycloak_user_id: str,
        user_settings: UserSettings,
    ) -> UserSettings | None:
        logger.info(
            'LiteLlmManager:migrate_lite_llm_entries:start',
            extra={'org_id': org_id, 'user_id': keycloak_user_id},
        )
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return None
        local_deploy = os.environ.get('LOCAL_DEPLOYMENT', None)
        if not local_deploy:
            async with httpx.AsyncClient(
                headers={
                    'x-goog-api-key': LITE_LLM_API_KEY,
                }
            ) as client:
                user_json = await LiteLlmManager._get_user(client, keycloak_user_id)
                if not user_json:
                    return None
                user_info = user_json['user_info']

                # Log original user values before any modifications for debugging
                original_max_budget = user_info.get('max_budget')
                original_spend = user_info.get('spend')
                logger.info(
                    'LiteLlmManager:migrate_lite_llm_entries:original_user_values',
                    extra={
                        'org_id': org_id,
                        'user_id': keycloak_user_id,
                        'original_max_budget': original_max_budget,
                        'original_spend': original_spend,
                    },
                )

                max_budget = (
                    original_max_budget if original_max_budget is not None else 0.0
                )
                spend = original_spend if original_spend is not None else 0.0
                # In upgrade to V4, we no longer use billing margin, but instead apply this directly
                # in litellm. The default billing marign was 2 before this (hence the magic numbers below)
                if (
                    user_settings
                    and user_settings.user_version < 4
                    and user_settings.billing_margin
                    and user_settings.billing_margin != 1.0
                ):
                    billing_margin = user_settings.billing_margin
                    logger.info(
                        'user_settings_v4_budget_upgrade',
                        extra={
                            'max_budget': max_budget,
                            'billing_margin': billing_margin,
                            'spend': spend,
                        },
                    )
                    max_budget *= billing_margin
                    spend *= billing_margin

                # max_budget=None or UNLIMITED means the user was already migrated.
                # Note: max_budget=0.0 (free tier) is distinct from None (no enforcement).
                if (
                    original_max_budget is None
                    or original_max_budget == UNLIMITED_BUDGET_SETTING
                ):
                    logger.info(
                        'LiteLlmManager:migrate_lite_llm_entries:already_migrated',
                        extra={
                            'org_id': org_id,
                            'user_id': keycloak_user_id,
                            'original_max_budget': original_max_budget,
                        },
                    )
                    return None
                credits = max(max_budget - spend, 0.0)

                logger.info(
                    'LiteLlmManager:migrate_lite_llm_entries:calculated_values',
                    extra={
                        'org_id': org_id,
                        'user_id': keycloak_user_id,
                        'adjusted_max_budget': max_budget,
                        'adjusted_spend': spend,
                        'calculated_credits': credits,
                        'new_user_max_budget': UNLIMITED_BUDGET_SETTING,
                    },
                )

                logger.debug(
                    'LiteLlmManager:migrate_lite_llm_entries:create_team',
                    extra={'org_id': org_id, 'user_id': keycloak_user_id},
                )
                team_alias = await LiteLlmManager._team_alias_for_org(
                    org_id, keycloak_user_id
                )
                await LiteLlmManager._create_team(client, team_alias, org_id, credits)

                logger.debug(
                    'LiteLlmManager:migrate_lite_llm_entries:update_user',
                    extra={'org_id': org_id, 'user_id': keycloak_user_id},
                )
                await LiteLlmManager._update_user(
                    client, keycloak_user_id, max_budget=UNLIMITED_BUDGET_SETTING
                )

                logger.debug(
                    'LiteLlmManager:migrate_lite_llm_entries:add_user_to_team',
                    extra={'org_id': org_id, 'user_id': keycloak_user_id},
                )
                await LiteLlmManager._add_user_to_team(
                    client, keycloak_user_id, org_id, credits
                )

                logger.debug(
                    'LiteLlmManager:migrate_lite_llm_entries:update_user_keys',
                    extra={'org_id': org_id, 'user_id': keycloak_user_id},
                )
                await LiteLlmManager._update_user_keys(
                    client,
                    keycloak_user_id,
                    team_id=org_id,
                )

                # If the database key doesn't exist in LiteLLM, generate a new one
                # to prevent verification failures later.
                db_key = None
                llm_base_url = None
                llm_cfg = (
                    (user_settings.agent_settings or {}).get('llm', {})
                    if user_settings
                    else {}
                )
                llm_base_url = llm_cfg.get('base_url')
                if llm_base_url == LITE_LLM_API_URL:
                    db_key = llm_cfg.get('api_key')
                    if db_key is not None and hasattr(db_key, 'get_secret_value'):
                        db_key = db_key.get_secret_value()

                if db_key:
                    key_valid = await LiteLlmManager.verify_key(
                        db_key, keycloak_user_id
                    )
                    if not key_valid:
                        logger.warning(
                            'LiteLlmManager:migrate_lite_llm_entries:db_key_not_in_litellm',
                            extra={
                                'org_id': org_id,
                                'user_id': keycloak_user_id,
                                'key_prefix': db_key[:10] + '...'
                                if len(db_key) > 10
                                else db_key,
                            },
                        )
                        new_key = await LiteLlmManager._generate_key(
                            client,
                            keycloak_user_id,
                            org_id,
                            get_openhands_cloud_key_alias(keycloak_user_id, org_id),
                            None,
                        )
                        logger.info(
                            'LiteLlmManager:migrate_lite_llm_entries:generated_new_key',
                            extra={'org_id': org_id, 'user_id': keycloak_user_id},
                        )
                        # agent_settings is a non-nullable JSON column (dict) on UserSettings
                        user_settings.agent_settings.setdefault('llm', {})[
                            'api_key'
                        ] = new_key
                        user_settings.llm_api_key_for_byor_secret = SecretStr(new_key)

        logger.info(
            'LiteLlmManager:migrate_lite_llm_entries:complete',
            extra={'org_id': org_id, 'user_id': keycloak_user_id},
        )
        return user_settings

    @staticmethod
    async def downgrade_entries(
        org_id: str,
        keycloak_user_id: str,
        user_settings: UserSettings,
    ) -> UserSettings | None:
        """Downgrade a migrated user's LiteLLM entries back to the pre-migration state.

        This reverses the migrate_entries operation:
        1. Get the user max budget from their org team in litellm
        2. Set the max budget in the user in litellm (restore from team)
        3. Add the user back to the default team in litellm
        4. Update keys to remove org team association
        5. Remove the user from their org team in litellm
        6. Delete the user org team in litellm

        Note: The database changes (already_migrated flag, org/org_member deletion)
        should be handled separately by the caller.

        Args:
            org_id: The organization ID (which is also the team_id in litellm)
            keycloak_user_id: The user's Keycloak ID
            user_settings: The user's settings object

        Returns:
            The user_settings if downgrade was successful, None otherwise
        """
        logger.info(
            'LiteLlmManager:downgrade_entries:start',
            extra={'org_id': org_id, 'user_id': keycloak_user_id},
        )
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return None

        local_deploy = os.environ.get('LOCAL_DEPLOYMENT', None)
        if not local_deploy:
            async with httpx.AsyncClient(
                headers={
                    'x-goog-api-key': LITE_LLM_API_KEY,
                }
            ) as client:
                # Step 1: Get the team info to retrieve the budget
                logger.debug(
                    'LiteLlmManager:downgrade_entries:get_team',
                    extra={'org_id': org_id, 'user_id': keycloak_user_id},
                )
                team_info = await LiteLlmManager._get_team(client, org_id)
                if not team_info:
                    logger.error(
                        'LiteLlmManager:downgrade_entries:team_not_found',
                        extra={'org_id': org_id, 'user_id': keycloak_user_id},
                    )
                    return None

                team_data = team_info.get('team_info', {})
                max_budget = team_data.get('max_budget', 0.0)
                spend = team_data.get('spend', 0.0)

                user_membership = await LiteLlmManager._get_user_team_info(
                    client, keycloak_user_id, org_id
                )
                if user_membership:
                    user_max_budget_in_team = user_membership.get('max_budget_in_team')
                    user_spend_in_team = user_membership.get('spend', 0.0)
                    if user_max_budget_in_team is not None:
                        max_budget = user_max_budget_in_team
                        spend = user_spend_in_team

                # Calculate total budget to restore (credits + spend = max_budget)
                # We restore the full max_budget that was on the team/user-in-team
                restored_budget = max_budget if max_budget else 0.0

                logger.debug(
                    'LiteLlmManager:downgrade_entries:budget_info',
                    extra={
                        'org_id': org_id,
                        'user_id': keycloak_user_id,
                        'max_budget': max_budget,
                        'spend': spend,
                        'restored_budget': restored_budget,
                    },
                )

                # Step 2: Update user to set their max_budget back from unlimited
                logger.debug(
                    'LiteLlmManager:downgrade_entries:update_user',
                    extra={'org_id': org_id, 'user_id': keycloak_user_id},
                )
                await LiteLlmManager._update_user(
                    client, keycloak_user_id, max_budget=restored_budget, spend=spend
                )

                # Step 3: Add user back to the default team
                if LITE_LLM_TEAM_ID:
                    logger.debug(
                        'LiteLlmManager:downgrade_entries:add_to_default_team',
                        extra={
                            'org_id': org_id,
                            'user_id': keycloak_user_id,
                            'default_team_id': LITE_LLM_TEAM_ID,
                        },
                    )
                    await LiteLlmManager._add_user_to_team(
                        client, keycloak_user_id, LITE_LLM_TEAM_ID, restored_budget
                    )

                # Step 4: Update all user keys to remove org team association (set team_id to default)
                logger.debug(
                    'LiteLlmManager:downgrade_entries:update_user_keys',
                    extra={'org_id': org_id, 'user_id': keycloak_user_id},
                )
                await LiteLlmManager._update_user_keys(
                    client,
                    keycloak_user_id,
                    team_id=LITE_LLM_TEAM_ID,
                )

                # Step 5: Remove user from their org team
                logger.debug(
                    'LiteLlmManager:downgrade_entries:remove_from_org_team',
                    extra={'org_id': org_id, 'user_id': keycloak_user_id},
                )
                await LiteLlmManager._remove_user_from_team(
                    client, keycloak_user_id, org_id
                )

                # Step 6: Delete the org team
                logger.debug(
                    'LiteLlmManager:downgrade_entries:delete_team',
                    extra={'org_id': org_id, 'user_id': keycloak_user_id},
                )
                await LiteLlmManager._delete_team(client, org_id)

        logger.info(
            'LiteLlmManager:downgrade_entries:complete',
            extra={'org_id': org_id, 'user_id': keycloak_user_id},
        )
        return user_settings

    @staticmethod
    async def update_team_and_users_budget(
        team_id: str,
        max_budget: float,
    ):
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return
        async with httpx.AsyncClient(
            headers={
                'x-goog-api-key': LITE_LLM_API_KEY,
            }
        ) as client:
            await LiteLlmManager._update_team(client, team_id, None, max_budget)
            team_info = await LiteLlmManager._get_team(client, team_id)
            if not team_info:
                return None
            # TODO: change to use bulk update endpoint
            for membership in team_info.get('team_memberships', []):
                user_id = membership.get('user_id')
                if not user_id:
                    continue
                await LiteLlmManager._update_user_in_team(
                    client, user_id, team_id, max_budget
                )

    @staticmethod
    async def ensure_free_team_models(org_id: str) -> bool:
        """Repair a free-tier team whose ``models`` allowlist is missing the
        current $0-cost set (e.g. after ``deepseek-v4-flash`` joined
        ``FREE_LLM_MODELS``). Idempotent and convergent.

        Returns True only when a write was performed. Never raises: LiteLLM
        being unreachable (or unconfigured in self-hosted installs) must not
        fail an org-version upgrade.
        """
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            return False
        try:
            async with httpx.AsyncClient(
                headers={'x-goog-api-key': LITE_LLM_API_KEY},
                timeout=httpx.Timeout(LITELLM_MANAGEMENT_TIMEOUT),
            ) as client:
                existing_team = await LiteLlmManager._get_team(client, org_id)
                if not existing_team:
                    return False
                team_info = existing_team.get('team_info', {})
                max_budget = team_info.get('max_budget')
                models = team_info.get('models') or []

                # Free tier is recognized by the same shape create_entries uses:
                # a cleared budget (None) with a non-empty restricted allowlist,
                # or an explicit zero budget. Anything else is a paid/unlimited
                # team and must not be touched.
                is_free = (
                    max_budget is None
                    and models
                    and set(models).issubset(set(FREE_LLM_MODELS))
                ) or (max_budget is not None and max_budget <= 0.0)
                if not is_free:
                    return False

                # Already converged: the team advertises the full current free
                # set, so there is nothing to repair and no write is needed.
                if set(FREE_LLM_MODELS).issubset(set(models)):
                    return False

                # Re-apply the canonical free-tier state (full free allowlist,
                # budget enforcement cleared) via the admin API, which also
                # invalidates LiteLLM's own team cache.
                await LiteLlmManager._update_team(client, org_id, None, 0.0)
                return True
        except Exception:
            logger.warning(
                'LiteLlmManager:ensure_free_team_models:failed',
                exc_info=True,
                extra={'org_id': org_id},
            )
            return False

    @staticmethod
    async def _team_alias_for_org(org_id: str, keycloak_user_id: str) -> str:
        """Resolve the dashboard-friendly team_alias for an org (its display
        name, or 'Personal Workspace' for the user's personal org). The org
        name is looked up lazily; lookup failures fall back to a stable label."""
        if str(org_id) == str(keycloak_user_id):
            return get_org_team_alias(org_id, None, keycloak_user_id)
        # Lazy import: org_store imports this module at load time.
        from uuid import UUID

        from storage.org_store import OrgStore

        org_name = None
        try:
            org = await OrgStore.get_org_by_id(UUID(org_id))
            org_name = org.name if org else None
        except Exception:
            logger.warning(
                'Failed to resolve org name for LiteLLM team_alias',
                extra={'org_id': org_id},
            )
        return get_org_team_alias(org_id, org_name, keycloak_user_id)

    @staticmethod
    async def _create_team(
        client: httpx.AsyncClient,
        team_alias: str,
        team_id: str,
        max_budget: float | None,
        free_models: list[str] | None = None,
    ):
        """Create a new team in LiteLLM.

        Args:
            client: The HTTP client to use.
            team_alias: The alias for the team.
            team_id: The ID for the team.
            max_budget: The maximum budget for the team. When None, budget
                enforcement is disabled (unlimited usage).
        """
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return

        json_data: dict[str, Any] = {
            'team_id': team_id,
            'team_alias': team_alias,
            'models': [],
            'spend': 0,
            'metadata': {
                'version': ORG_SETTINGS_VERSION,
                'model': get_default_litellm_model(),
            },
        }

        if _is_free_budget(max_budget):
            if free_models is None:
                free_models = await LiteLlmManager._resolve_free_llm_models()
            json_data['models'] = _litellm_free_model_allowlist(free_models)
        elif max_budget is not None:
            json_data['max_budget'] = max_budget

        response = await client.post(
            f'{LITE_LLM_API_URL}/team/new',
            json=json_data,
        )

        # Team failed to create in litellm - this is an unforeseen error state...
        if not response.is_success:
            if (
                response.status_code == 400
                and 'already exists. Please use a different team id' in response.text
            ):
                # Provisioning is not authority to replace an existing team's policy.
                return
            logger.error(
                'error_creating_litellm_team',
                extra={
                    'status_code': response.status_code,
                    'text': response.text,
                    'team_id': team_id,
                    'max_budget': max_budget,
                },
            )
        response.raise_for_status()

    @staticmethod
    async def _get_team(client: httpx.AsyncClient, team_id: str) -> dict | None:
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return None
        """Get a team from litellm with the id matching that given."""
        response = await client.get(
            f'{LITE_LLM_API_URL}/team/info?team_id={team_id}',
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    async def _update_team(
        client: httpx.AsyncClient,
        team_id: str,
        team_alias: str | None,
        max_budget: float | None,
        clear_budget: bool = False,
        free_models: list[str] | None = None,
    ):
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return
        json_data: dict[str, Any] = {
            'team_id': team_id,
            'metadata': {
                'version': ORG_SETTINGS_VERSION,
                'model': get_default_litellm_model(),
            },
        }

        if _is_free_budget(max_budget):
            if free_models is None:
                free_models = await LiteLlmManager._resolve_free_llm_models()
            json_data['max_budget'] = None
            json_data['models'] = _litellm_free_model_allowlist(free_models)
        elif max_budget is not None or clear_budget:
            # Paid tier (or explicit clear): (re)open the full model list and
            # set the budget. ``models: []`` is LiteLLM's "all models" sentinel.
            json_data['max_budget'] = max_budget
            json_data['models'] = []

        if team_alias is not None:
            json_data['team_alias'] = team_alias

        response = await client.post(
            f'{LITE_LLM_API_URL}/team/update',
            json=json_data,
        )

        # Team failed to update in litellm - this is an unforeseen error state...
        if not response.is_success:
            logger.error(
                'error_updating_litellm_team',
                extra={
                    'status_code': response.status_code,
                    'text': response.text,
                    'team_id': [team_id],
                    'max_budget': max_budget,
                },
            )
        response.raise_for_status()

    @staticmethod
    async def _user_exists(
        client: httpx.AsyncClient,
        user_id: str,
    ) -> bool:
        """Check if a user exists in LiteLLM.

        Returns True if the user exists, False otherwise.
        """
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            return False
        try:
            response = await client.get(
                f'{LITE_LLM_API_URL}/user/info?user_id={user_id}',
            )
            if response.is_success:
                user_data = response.json()
                user_info = user_data.get('user_info', {})
                return user_info.get('user_id') == user_id
            return False
        except Exception as e:
            logger.warning(
                'litellm_user_exists_check_failed',
                extra={'user_id': user_id, 'error': str(e)},
            )
            return False

    @staticmethod
    async def _create_user(
        client: httpx.AsyncClient,
        email: str | None,
        keycloak_user_id: str,
    ) -> bool:
        """Create a user in LiteLLM.

        Returns True if the user was created or already exists and is verified,
        False if creation failed and user does not exist.
        """
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return False
        response = await client.post(
            f'{LITE_LLM_API_URL}/user/new',
            json={
                'user_email': email,
                'models': [],
                'user_id': keycloak_user_id,
                'teams': [LITE_LLM_TEAM_ID],
                'auto_create_key': False,
                'send_invite_email': False,
                'metadata': {
                    'version': ORG_SETTINGS_VERSION,
                    'model': get_default_litellm_model(),
                },
            },
        )
        if not response.is_success:
            logger.warning(
                'duplicate_user_email',
                extra={
                    'user_id': keycloak_user_id,
                    'email': email,
                },
            )
            # Litellm insists on unique email addresses - it is possible the email address was registered with a different user.
            response = await client.post(
                f'{LITE_LLM_API_URL}/user/new',
                json={
                    'user_email': None,
                    'models': [],
                    'user_id': keycloak_user_id,
                    'teams': [LITE_LLM_TEAM_ID],
                    'auto_create_key': False,
                    'send_invite_email': False,
                    'metadata': {
                        'version': ORG_SETTINGS_VERSION,
                        'model': get_default_litellm_model(),
                    },
                },
            )

            # User failed to create in litellm - this is an unforeseen error state...
            if not response.is_success:
                if (
                    response.status_code in (400, 409)
                    and 'already exists' in response.text
                ):
                    logger.warning(
                        'litellm_user_already_exists',
                        extra={
                            'user_id': keycloak_user_id,
                        },
                    )
                    # Verify the user actually exists before returning success
                    user_exists = await LiteLlmManager._user_exists(
                        client, keycloak_user_id
                    )
                    if not user_exists:
                        logger.error(
                            'litellm_user_claimed_exists_but_not_found',
                            extra={
                                'user_id': keycloak_user_id,
                                'status_code': response.status_code,
                                'text': response.text,
                            },
                        )
                        return False
                    return True
                logger.error(
                    'error_creating_litellm_user',
                    extra={
                        'status_code': response.status_code,
                        'text': response.text,
                        'user_id': keycloak_user_id,
                        'email': None,
                    },
                )
                return False
            response.raise_for_status()
        return True

    @staticmethod
    async def _get_user(client: httpx.AsyncClient, user_id: str) -> dict | None:
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return None
        """Get a user from litellm with the id matching that given."""
        response = await client.get(
            f'{LITE_LLM_API_URL}/user/info?user_id={user_id}',
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    async def _update_user(
        client: httpx.AsyncClient,
        keycloak_user_id: str,
        **kwargs,
    ):
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return

        payload = {
            'user_id': keycloak_user_id,
        }
        payload.update(kwargs)

        response = await client.post(
            f'{LITE_LLM_API_URL}/user/update',
            json=payload,
        )

        if not response.is_success:
            logger.error(
                'error_updating_litellm_user',
                extra={
                    'status_code': response.status_code,
                    'text': response.text,
                    'user_id': keycloak_user_id,
                },
            )
        response.raise_for_status()

    @staticmethod
    async def _update_key(
        client: httpx.AsyncClient,
        keycloak_user_id: str,
        key: str,
        **kwargs,
    ):
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return

        payload = {
            'key': key,
        }
        payload.update(kwargs)

        response = await client.post(
            f'{LITE_LLM_API_URL}/key/update',
            json=payload,
        )

        if not response.is_success:
            if response.status_code == 401:
                logger.warning(
                    'invalid_litellm_key_during_update',
                    extra={
                        'user_id': keycloak_user_id,
                        'text': response.text,
                    },
                )
                return
            logger.error(
                'error_updating_litellm_key',
                extra={
                    'status_code': response.status_code,
                    'text': response.text,
                    'user_id': keycloak_user_id,
                },
            )
        response.raise_for_status()

    @staticmethod
    async def _get_user_keys(
        client: httpx.AsyncClient,
        keycloak_user_id: str,
    ) -> list[str]:
        """Get all keys for a user from LiteLLM.

        Args:
            client: The HTTP client to use for the request
            keycloak_user_id: The user's Keycloak ID

        Returns:
            A list of key strings belonging to the user
        """
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return []

        response = await client.get(
            f'{LITE_LLM_API_URL}/key/list',
            params={'user_id': keycloak_user_id},
        )

        if not response.is_success:
            logger.error(
                'error_getting_user_keys',
                extra={
                    'status_code': response.status_code,
                    'text': response.text,
                    'user_id': keycloak_user_id,
                },
            )
            return []

        response_json = response.json()
        keys = response_json.get('keys', [])
        logger.debug(
            'LiteLlmManager:_get_user_keys:keys_retrieved',
            extra={
                'user_id': keycloak_user_id,
                'key_count': len(keys),
            },
        )
        return keys

    @staticmethod
    async def _update_user_keys(
        client: httpx.AsyncClient,
        keycloak_user_id: str,
        **kwargs,
    ):
        """Update all keys belonging to a user with the given parameters.

        Args:
            client: The HTTP client to use for the request
            keycloak_user_id: The user's Keycloak ID
            **kwargs: Parameters to update on each key (e.g., team_id)
        """
        keys = await LiteLlmManager._get_user_keys(client, keycloak_user_id)

        logger.debug(
            'LiteLlmManager:_update_user_keys:updating_keys',
            extra={
                'user_id': keycloak_user_id,
                'key_count': len(keys),
            },
        )

        for key in keys:
            await LiteLlmManager._update_key(client, keycloak_user_id, key, **kwargs)

    @staticmethod
    async def _delete_user(
        client: httpx.AsyncClient,
        keycloak_user_id: str,
    ):
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return
        response = await client.post(
            f'{LITE_LLM_API_URL}/user/delete', json={'user_ids': [keycloak_user_id]}
        )

        if not response.is_success:
            if response.status_code == 404:
                # User doesn't exist - delete is idempotent (mirrors _delete_team).
                logger.info(
                    'LiteLlmManager:_delete_user:already_deleted_or_missing',
                    extra={'user_id': keycloak_user_id},
                )
                return
            logger.error(
                'error_deleting_litellm_user',
                extra={
                    'status_code': response.status_code,
                    'text': response.text,
                    'user_id': [keycloak_user_id],
                },
            )
        response.raise_for_status()

    @staticmethod
    async def _delete_team(
        client: httpx.AsyncClient,
        team_id: str,
    ):
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return
        response = await client.post(
            f'{LITE_LLM_API_URL}/team/delete',
            json={'team_ids': [team_id]},
        )

        if not response.is_success:
            if response.status_code == 404:
                # Team doesn't exist, that's fine
                logger.info(
                    'Team already deleted or does not exist',
                    extra={'team_id': team_id},
                )
                return
            logger.error(
                'error_deleting_litellm_team',
                extra={
                    'status_code': response.status_code,
                    'text': response.text,
                    'team_id': team_id,
                },
            )
        response.raise_for_status()
        logger.info(
            'LiteLlmManager:_delete_team:team_deleted',
            extra={'team_id': team_id},
        )

    @staticmethod
    async def _add_user_to_team(
        client: httpx.AsyncClient,
        keycloak_user_id: str,
        team_id: str,
        max_budget: float | None,
    ):
        """Add a user to a team in LiteLLM.

        Args:
            client: The HTTP client to use.
            keycloak_user_id: The user's Keycloak ID.
            team_id: The team ID.
            max_budget: The maximum budget for the user in the team. When None,
                budget enforcement is disabled (unlimited usage).
        """
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return

        json_data: dict[str, Any] = {
            'team_id': team_id,
            'member': {'user_id': keycloak_user_id, 'role': 'user'},
        }

        if _is_free_budget(max_budget):
            # Free tier: the team's model restriction already limits the member
            # to $0-cost models, so leave the member budget unset (no
            # enforcement) rather than gating them at 0.0.
            pass
        elif max_budget is not None:
            json_data['max_budget_in_team'] = max_budget

        response = await client.post(
            f'{LITE_LLM_API_URL}/team/member_add',
            json=json_data,
        )

        # Failed to add user to team - this is an unforeseen error state...
        if not response.is_success:
            if (
                response.status_code == 400
                and 'already in team' in response.text.lower()
            ):
                logger.warning(
                    'user_already_in_team',
                    extra={
                        'user_id': keycloak_user_id,
                        'team_id': team_id,
                    },
                )
                return
            logger.error(
                'error_adding_litellm_user_to_team',
                extra={
                    'status_code': response.status_code,
                    'text': response.text,
                    'user_id': [keycloak_user_id],
                    'team_id': [team_id],
                    'max_budget': max_budget,
                },
            )
        response.raise_for_status()

    @staticmethod
    def _member_dict(member: Any) -> dict[str, Any]:
        if isinstance(member, dict):
            return dict(member)

        model_dump = getattr(member, 'model_dump', None)
        if callable(model_dump):
            return model_dump()

        if isinstance(member, str):
            return {'user_id': member}

        values: dict[str, Any] = {}
        for field in (
            'user_id',
            'user_email',
            'role',
            'team_id',
            'budget_id',
            'spend',
            'max_budget_in_team',
            'litellm_budget_table',
        ):
            if hasattr(member, field):
                values[field] = getattr(member, field)
        return values

    @staticmethod
    def _team_member_rows(team_response: dict[str, Any]) -> list[dict[str, Any]]:
        team_memberships = team_response.get('team_memberships') or []
        if team_memberships:
            return [
                LiteLlmManager._member_dict(membership)
                for membership in team_memberships
            ]

        team_info = team_response.get('team_info') or {}
        members_with_roles = team_info.get('members_with_roles') or []
        return [
            LiteLlmManager._member_dict(membership) for membership in members_with_roles
        ]

    @staticmethod
    async def _get_user_team_info(
        client: httpx.AsyncClient,
        keycloak_user_id: str,
        team_id: str,
    ) -> dict | None:
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return None
        team_response = await LiteLlmManager._get_team(client, team_id)
        if not team_response:
            return None

        user_membership = next(
            (
                membership
                for membership in LiteLlmManager._team_member_rows(team_response)
                if membership.get('user_id') == keycloak_user_id
                and membership.get('team_id', team_id) == team_id
            ),
            None,
        )

        if not user_membership:
            return None

        team_info = team_response.get('team_info', {})
        if keycloak_user_id != team_id:
            if 'max_budget' not in team_info or 'spend' not in team_info:
                return None
            user_membership['max_budget_in_team'] = team_info['max_budget']
            user_membership['spend'] = team_info['spend']
        elif 'spend' not in user_membership:
            # A personal workspace belongs to one user, so the team's budget is
            # that user's balance; store it the way a per-member budget would.
            if 'max_budget' not in team_info or 'spend' not in team_info:
                return None
            user_membership['litellm_budget_table'] = {
                'max_budget': team_info['max_budget']
            }
            user_membership['spend'] = team_info['spend']

        return user_membership

    @staticmethod
    async def _update_user_in_team(
        client: httpx.AsyncClient,
        keycloak_user_id: str,
        team_id: str,
        max_budget: float | None,
        clear_budget: bool = False,
    ):
        """Update a user's budget in a team.

        Args:
            client: The HTTP client to use.
            keycloak_user_id: The user's Keycloak ID.
            team_id: The team ID.
            max_budget: The maximum budget for the user in the team. When None,
                budget enforcement is disabled (unlimited usage).
        """
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return

        json_data: dict[str, Any] = {
            'team_id': team_id,
            'user_id': keycloak_user_id,
        }

        if _is_free_budget(max_budget):
            # Free tier: clear any stale member budget so it can't gate the
            # team's already-restricted $0-cost model set.
            json_data['max_budget_in_team'] = None
        elif max_budget is not None or clear_budget:
            json_data['max_budget_in_team'] = max_budget

        response = await client.post(
            f'{LITE_LLM_API_URL}/team/member_update',
            json=json_data,
        )

        # Failed to update user in team - this is an unforeseen error state...
        if not response.is_success:
            logger.error(
                'error_updating_litellm_user_in_team',
                extra={
                    'status_code': response.status_code,
                    'text': response.text,
                    'user_id': [keycloak_user_id],
                    'team_id': [team_id],
                    'max_budget': max_budget,
                },
            )
        response.raise_for_status()

    @staticmethod
    async def _remove_user_from_team(
        client: httpx.AsyncClient,
        keycloak_user_id: str,
        team_id: str,
    ):
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return
        response = await client.post(
            f'{LITE_LLM_API_URL}/team/member_delete',
            json={
                'team_id': team_id,
                'user_id': keycloak_user_id,
            },
        )
        if not response.is_success:
            if response.status_code == 404:
                # User not in team, that's fine for downgrade
                logger.info(
                    'User not in team during removal',
                    extra={'user_id': keycloak_user_id, 'team_id': team_id},
                )
                return
            logger.error(
                'error_removing_litellm_user_from_team',
                extra={
                    'status_code': response.status_code,
                    'text': response.text,
                    'user_id': keycloak_user_id,
                    'team_id': team_id,
                },
            )
        response.raise_for_status()
        logger.info(
            'LiteLlmManager:_remove_user_from_team:user_removed',
            extra={'user_id': keycloak_user_id, 'team_id': team_id},
        )

    @staticmethod
    async def _generate_key(
        client: httpx.AsyncClient,
        keycloak_user_id: str,
        team_id: str | None,
        key_alias: str | None,
        metadata: dict | None,
    ) -> str:
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            raise ValueError('LiteLLM API configuration not found')
        async with key_mutation_scope(team_id):
            await LiteLlmManager._check_key_creation(
                client, keycloak_user_id, team_id, key_alias
            )
            payload: dict[str, Any] = {
                'user_id': keycloak_user_id,
                'team_id': team_id,
                'models': [],
            }
            if key_alias is not None:
                payload['key_alias'] = key_alias
            if metadata is not None:
                payload['metadata'] = metadata
            response = await client.post(
                f'{LITE_LLM_API_URL}/key/generate', json=payload
            )
            response.raise_for_status()
            key = response.json().get('key')
            if not isinstance(key, str) or not key:
                raise RuntimeError('LiteLLM did not return a credential')
            return key

    @staticmethod
    async def _check_key_creation(
        client: httpx.AsyncClient,
        user_id: str,
        team_id: str | None,
        key_alias: str | None,
    ) -> None:
        from storage.budget_control import BudgetWriteDenied

        keys = await LiteLlmManager._get_all_keys_for_user(client, user_id)
        if keys is None:
            raise BudgetWriteDenied(
                'Cannot create a credential while key policy is unavailable'
            )
        for key in keys:
            if key.get('team_id') is None and key_restrictions(key):
                raise BudgetWriteDenied(
                    'Unscoped key policy requires explicit credential recovery'
                )
            if key.get('team_id') != team_id:
                continue
            if (
                key.get('user_id') != user_id
                or not isinstance(key.get('token'), str)
                or not key['token']
            ):
                raise BudgetWriteDenied('Key ownership could not be established')
            if key.get('key_alias') == key_alias or key_restrictions(key):
                raise BudgetWriteDenied(
                    'Existing key policy requires explicit credential recovery; it was not replaced'
                )
        if key_alias:
            response = await client.get(
                f'{LITE_LLM_API_URL}/key/list',
                params={'key_alias': key_alias, 'return_full_object': True, 'size': 1},
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or not isinstance(result.get('keys'), list):
                raise BudgetWriteDenied(
                    'Unable to inspect the existing credential alias'
                )
            count = result.get('total_count')
            if type(count) is not int or count < 0:
                raise BudgetWriteDenied(
                    'Unable to inspect the existing credential alias'
                )
            if result['keys'] or count != 0:
                raise BudgetWriteDenied(
                    'Credential alias already exists; no key was deleted'
                )

    @staticmethod
    async def ensure_managed_key(
        keycloak_user_id: str,
        org_id: str,
        existing_key: str | None,
        *,
        openhands_type: bool = False,
    ) -> str:
        """Reuse owned credentials, including operator-blocked or renamed keys."""
        if existing_key and await LiteLlmManager.verify_existing_key(
            existing_key, keycloak_user_id, org_id, openhands_type=openhands_type
        ):
            return existing_key
        return await LiteLlmManager.generate_key(
            keycloak_user_id,
            org_id,
            get_openhands_cloud_key_alias(keycloak_user_id, org_id),
            {'type': 'openhands'} if openhands_type else None,
        )

    @staticmethod
    async def verify_key(key: str, user_id: str) -> bool:
        """Verify that a key is valid in LiteLLM by making a lightweight API call.

        Args:
            key: The key to verify
            user_id: The user ID for logging purposes

        Returns:
            True if the key is verified as valid or verification is inconclusive.
            False only when LiteLLM explicitly reports an auth failure.
        """
        if not (LITE_LLM_API_URL and key):
            return False

        def _extract_error_message(response: httpx.Response) -> str:
            try:
                message = response.text or ''
            except Exception:
                message = ''
            if message:
                return message
            try:
                payload = response.json()
            except Exception:
                return ''
            if isinstance(payload, dict):
                for field in ('detail', 'error', 'message'):
                    value = payload.get(field)
                    if isinstance(value, str):
                        return value
            return ''

        def _is_budget_exceeded(message: str) -> bool:
            lower = message.lower()
            return 'budget' in lower and 'exceeded' in lower

        try:
            async with httpx.AsyncClient(
                verify=httpx_verify_option(),
                timeout=KEY_VERIFICATION_TIMEOUT,
            ) as client:
                # Using /v1/models endpoint as it's lightweight and requires authentication
                response = await client.get(
                    f'{LITE_LLM_API_URL}/v1/models',
                    headers={
                        'Authorization': f'Bearer {key}',
                    },
                )

                if response.status_code == 200:
                    logger.debug(
                        'Key verification successful',
                        extra={'user_id': user_id},
                    )
                    return True

                if response.status_code in (401, 403):
                    logger.warning(
                        'Key verification failed - invalid credentials',
                        extra={
                            'user_id': user_id,
                            'status_code': response.status_code,
                            'key_prefix': key[:10] + '...' if len(key) > 10 else key,
                        },
                    )
                    return False

                error_message = _extract_error_message(response)
                if response.status_code == 400 and _is_budget_exceeded(error_message):
                    logger.info(
                        'Key verification blocked by budget exceeded - preserving key',
                        extra={
                            'user_id': user_id,
                            'status_code': response.status_code,
                            'key_prefix': key[:10] + '...' if len(key) > 10 else key,
                        },
                    )
                    return True

                logger.warning(
                    'Key verification inconclusive - preserving key',
                    extra={
                        'user_id': user_id,
                        'status_code': response.status_code,
                        'key_prefix': key[:10] + '...' if len(key) > 10 else key,
                    },
                )
                return True

        except (httpx.TimeoutException, Exception) as e:
            logger.warning(
                'Key verification error - preserving key',
                extra={
                    'user_id': user_id,
                    'error': str(e),
                    'error_type': type(e).__name__,
                },
            )
            return True

    @staticmethod
    async def _get_key_info(
        client: httpx.AsyncClient,
        org_id: str,
        keycloak_user_id: str,
    ) -> dict | None:
        from storage.user_store import UserStore

        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return None
        user = await UserStore.get_user_by_id(keycloak_user_id)
        if not user:
            return {}

        org_member = None
        for om in user.org_members:
            if om.org_id == org_id:
                org_member = om
                break
        if not org_member or not org_member.llm_api_key:
            return {}
        response = await client.get(
            f'{LITE_LLM_API_URL}/key/info?key={org_member.llm_api_key}'
        )
        response.raise_for_status()
        response_json = response.json()
        key_info = response_json.get('info')
        if not key_info:
            return {}
        return {
            'key_max_budget': key_info.get('max_budget'),
            'key_spend': key_info.get('spend'),
        }

    @staticmethod
    async def _get_all_keys_for_user(
        client: httpx.AsyncClient,
        keycloak_user_id: str,
    ) -> list[dict] | None:
        """Get all keys for a user from LiteLLM.

        Returns a list of key info dictionaries containing:
        - token: the key value (hashed or partial)
        - key_alias: the alias for the key
        - key_name: the name of the key
        - spend: the amount spent on this key
        - max_budget: the max budget for this key
        - team_id: the team the key belongs to
        - metadata: any metadata associated with the key

        Returns None when the LiteLLM request fails so callers can skip
        invalidation on transient errors.
        """
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return None

        try:
            response = await client.get(
                f'{LITE_LLM_API_URL}/user/info?user_id={keycloak_user_id}',
                headers={'x-goog-api-key': LITE_LLM_API_KEY},
            )
        except Exception as e:
            logger.warning(
                'LiteLlmManager:_get_all_keys_for_user:request_error',
                extra={
                    'user_id': keycloak_user_id,
                    'error': str(e),
                },
            )
            return None

        if response.status_code == 404:
            logger.info(
                'LiteLlmManager:_get_all_keys_for_user:not_found',
                extra={'user_id': keycloak_user_id},
            )
            return []

        if not response.is_success:
            logger.warning(
                'LiteLlmManager:_get_all_keys_for_user:request_failed',
                extra={
                    'user_id': keycloak_user_id,
                    'status_code': response.status_code,
                    'text': response.text,
                },
            )
            return None

        try:
            user_json = response.json()
        except Exception as e:
            logger.warning(
                'LiteLlmManager:_get_all_keys_for_user:parse_failed',
                extra={
                    'user_id': keycloak_user_id,
                    'error': str(e),
                },
            )
            return None

        keys = user_json.get('keys') if isinstance(user_json, dict) else None
        if not isinstance(keys, list) or any(not isinstance(key, dict) for key in keys):
            return None
        return keys

    @staticmethod
    async def _verify_existing_key(
        client: httpx.AsyncClient,
        key_value: str,
        keycloak_user_id: str,
        org_id: str,
        openhands_type: bool = False,
    ) -> bool:
        """Verify full key identity; aliases and model metadata are not ownership."""
        keys = await LiteLlmManager._get_all_keys_for_user(client, keycloak_user_id)
        if keys is None:
            raise RuntimeError(
                'Unable to inspect LiteLLM keys without replacing policy'
            )
        return LiteLlmManager._key_belongs_to_user_org(
            keys,
            key_value,
            keycloak_user_id,
            org_id,
            openhands_type,
        )

    @staticmethod
    def _key_belongs_to_user_org(
        keys: list[dict],
        key_value: str,
        keycloak_user_id: str,
        org_id: str,
        openhands_type: bool,
    ) -> bool:
        del openhands_type
        if not key_value:
            return False
        key_hash = hashlib.sha256(key_value.encode()).hexdigest()
        for key_info in keys:
            token = key_info.get('token')
            if (
                key_info.get('user_id') == keycloak_user_id
                and key_info.get('team_id') == org_id
                and isinstance(token, str)
                and (
                    hmac.compare_digest(token.encode(), key_hash.encode())
                    or hmac.compare_digest(token.encode(), key_value.encode())
                )
            ):
                return True
        return False

    @staticmethod
    async def _verify_existing_key_strict(
        client: httpx.AsyncClient,
        key_value: str,
        keycloak_user_id: str,
        org_id: str,
        openhands_type: bool = False,
    ) -> bool:
        """Verify ownership without treating an unavailable lookup as healthy."""
        return await LiteLlmManager._verify_existing_key(
            client,
            key_value,
            keycloak_user_id,
            org_id,
            openhands_type,
        )

    @staticmethod
    async def _delete_key_by_alias(
        client: httpx.AsyncClient,
        key_alias: str,
    ):
        """Delete a key from LiteLLM by its alias.

        This is a best-effort operation that logs but does not raise on failure.
        """
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return
        response = await client.post(
            f'{LITE_LLM_API_URL}/key/delete',
            json={
                'key_aliases': [key_alias],
            },
        )
        if response.is_success:
            logger.info(
                'LiteLlmManager:_delete_key_by_alias:key_deleted',
                extra={'key_alias': key_alias},
            )
        elif response.status_code != 404:
            # Log non-404 errors but don't fail
            logger.warning(
                'error_deleting_key_by_alias',
                extra={
                    'key_alias': key_alias,
                    'status_code': response.status_code,
                    'text': response.text,
                },
            )

    @staticmethod
    async def _delete_key_by_alias_strict(
        client: httpx.AsyncClient,
        key_alias: str,
    ) -> None:
        """Delete a deterministic alias or fail without rotating the DB row."""
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            raise ValueError('LiteLLM API configuration not found')
        response = await client.post(
            f'{LITE_LLM_API_URL}/key/delete',
            json={'key_aliases': [key_alias]},
        )
        if response.status_code == 404:
            return
        response.raise_for_status()

    @staticmethod
    async def _delete_key(
        client: httpx.AsyncClient,
        key_id: str,
        key_alias: str | None = None,
    ):
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return
        response = await client.post(
            f'{LITE_LLM_API_URL}/key/delete',
            json={
                'keys': [key_id],
            },
        )
        # Failed to delete key...
        if not response.is_success:
            if response.status_code == 404:
                # The alias may now belong to a different credential.
                return
            logger.error(
                'error_deleting_key',
                extra={
                    'status_code': response.status_code,
                    'text': response.text,
                },
            )
        response.raise_for_status()
        logger.info(
            'LiteLlmManager:_delete_key:key_deleted',
        )

    @staticmethod
    async def _get_team_members_financial_data(
        client: httpx.AsyncClient,
        team_id: str,
        *,
        include_control_policy: bool = False,
    ) -> dict:
        """
        Get financial data for all members in a team.

        Fetches team info from LiteLLM and extracts spending/budget data for each member.

        Args:
            client: HTTP client for LiteLLM API
            team_id: The team/organization ID

        Returns:
            Dict with structure:
            {
                "team_max_budget": float | None,  # Team's shared budget
                "team_spend": float,              # Team's total spend (for shared budget calc)
                "members": {
                    user_id: {
                        "spend": float,
                        "max_budget": float | None,
                        "uses_shared_budget": bool  # True if using team budget
                    },
                    ...
                }
            }
            Returns empty dict if team not found or LiteLLM is not configured.
        """
        if LITE_LLM_API_KEY is None or LITE_LLM_API_URL is None:
            logger.warning('LiteLLM API configuration not found')
            return {}

        team_info = await LiteLlmManager._get_team(client, team_id)
        if not team_info:
            logger.warning(
                'LiteLlmManager:_get_team_members_financial_data:team_not_found',
                extra={'team_id': team_id},
            )
            return {}

        members: dict[str, dict] = {}
        team_memberships = [
            LiteLlmManager._member_dict(membership)
            for membership in team_info.get('team_memberships') or []
        ]

        # Get team-level budget info (shared across all members in team orgs)
        team_data = team_info.get('team_info')
        if not isinstance(team_data, dict):
            raise ValueError('LiteLLM team response is missing team_info')
        if 'max_budget' not in team_data or 'spend' not in team_data:
            raise ValueError(
                'LiteLLM team_info is missing required budget fields '
                '(max_budget, spend)'
            )
        team_max_budget = _validated_budget_number(
            team_data['max_budget'], 'team max_budget'
        )
        team_spend = team_data['spend']

        metadata = team_data.get('metadata') or {}
        if not isinstance(metadata, dict):
            raise ValueError('LiteLLM team_info.metadata must be an object')
        default_member_budget_id = metadata.get('team_member_budget_id')
        if default_member_budget_id is not None and not isinstance(
            default_member_budget_id, str
        ):
            raise ValueError(
                'LiteLLM team_info.metadata.team_member_budget_id must be a string'
            )

        default_budget = next(
            (
                membership.get('litellm_budget_table')
                for membership in team_memberships
                if default_member_budget_id is not None
                and membership.get('budget_id') == default_member_budget_id
            ),
            None,
        )
        member_counters: dict[str, dict[str, Any]] = {}

        membership_by_user_id: dict[str, dict[str, Any]] = {}
        for membership in team_memberships:
            membership_user_id = membership.get('user_id')
            if (
                isinstance(membership_user_id, str)
                and membership_user_id
                and membership_user_id != 'default_user_id'
            ):
                membership_by_user_id[membership_user_id] = membership

        role_member_ids: set[str] = set()
        for role_member in team_data.get('members_with_roles') or []:
            member_user_id = LiteLlmManager._member_dict(role_member).get('user_id')
            if (
                isinstance(member_user_id, str)
                and member_user_id
                and member_user_id != 'default_user_id'
            ):
                role_member_ids.add(member_user_id)

        # A normal LiteLLM team can contain roster members without a
        # LiteLLM_TeamMembership row until the first private cap is assigned.
        # Their key counters are the only authoritative per-user source at that
        # point. Membership counters take precedence as soon as a row exists.
        role_only_member_ids = role_member_ids - membership_by_user_id.keys()
        needs_default_budget = default_member_budget_id is not None and (
            include_control_policy
            or bool(role_only_member_ids)
            or any(
                not membership.get('litellm_budget_table')
                or membership['litellm_budget_table'].get('max_budget') is None
                for membership in membership_by_user_id.values()
            )
        )
        if needs_default_budget and default_budget is None:
            response = await client.post(
                f'{LITE_LLM_API_URL}/budget/info',
                json={'budgets': [default_member_budget_id]},
            )
            response.raise_for_status()
            budgets = response.json()
            if not isinstance(budgets, list) or len(budgets) != 1:
                raise ValueError('LiteLLM default member budget could not be read')
            default_budget = budgets[0]
            if (
                not isinstance(default_budget, dict)
                or default_budget.get('budget_id') != default_member_budget_id
            ):
                raise ValueError('LiteLLM default member budget identity mismatch')
        if default_budget is not None and (
            not isinstance(default_budget, dict) or 'max_budget' not in default_budget
        ):
            raise ValueError('LiteLLM default member budget is missing max_budget')
        default_cap = (
            _validated_budget_number(default_budget['max_budget'], 'default max_budget')
            if default_budget is not None
            else None
        )
        role_only_spend: dict[str, float] = {}
        if role_only_member_ids:
            keys = team_info.get('keys')
            if not isinstance(keys, list):
                raise ValueError('LiteLLM team response is missing keys')
            key_count_by_user: dict[str, int] = {}
            for key in keys:
                key_data = LiteLlmManager._member_dict(key)
                user_id = key_data.get('user_id')
                if not isinstance(user_id, str) or user_id not in role_only_member_ids:
                    continue
                spend = key_data.get('spend')
                if (
                    isinstance(spend, bool)
                    or not isinstance(spend, int | float)
                    or not math.isfinite(float(spend))
                    or spend < 0
                ):
                    raise ValueError(
                        f'LiteLLM key for role-only member {user_id} has invalid spend'
                    )
                role_only_spend[user_id] = role_only_spend.get(user_id, 0.0) + float(
                    spend
                )
                key_count_by_user[user_id] = key_count_by_user.get(user_id, 0) + 1

            missing_key_spend = role_only_member_ids - key_count_by_user.keys()
            if missing_key_spend:
                raise ValueError(
                    'LiteLLM role-only members have no validated key spend: '
                    + ', '.join(sorted(missing_key_spend))
                )

        for user_id, membership in membership_by_user_id.items():
            if 'spend' not in membership or membership['spend'] is None:
                raise ValueError(
                    f'LiteLLM membership {user_id} is missing required spend data'
                )

            budget_id = membership.get('budget_id')
            budget_table = membership.get('litellm_budget_table')
            if budget_id is not None:
                if not isinstance(budget_table, dict):
                    raise ValueError(
                        f'LiteLLM membership {user_id} is missing its budget table'
                    )
                if 'max_budget' not in budget_table:
                    raise ValueError(
                        f'LiteLLM membership {user_id} budget table is missing max_budget'
                    )
            member_max_budget = _validated_budget_number(
                budget_table['max_budget'] if budget_table is not None else None,
                f'member {user_id} max_budget',
            )
            budget_source = 'private_member'
            effective_budget_id = budget_id
            if budget_id == default_member_budget_id:
                budget_source = 'default_member'
            if (
                member_max_budget is None
                and default_cap is not None
                and default_cap > 0
            ):
                member_max_budget = default_cap
                budget_source = 'default_member'
                effective_budget_id = default_member_budget_id
            uses_shared_budget = member_max_budget is None
            if uses_shared_budget:
                member_max_budget = team_max_budget
                budget_source = 'team'

            members[user_id] = {
                'spend': membership['spend'],
                'max_budget': member_max_budget,
                'uses_shared_budget': uses_shared_budget,
            }
            member_counters[user_id] = {
                'source': 'membership',
                'spend': membership['spend'],
                'budget_id': budget_id,
                'effective_budget_id': effective_budget_id,
                'budget_source': budget_source,
                'budget_duration': (budget_table or {}).get('budget_duration'),
                'budget_reset_at': (budget_table or {}).get('budget_reset_at'),
                'reset_known': budget_id is None
                or {'budget_duration', 'budget_reset_at'}
                <= (budget_table or {}).keys(),
            }

        for user_id, spend in role_only_spend.items():
            uses_default_budget = default_cap is not None and default_cap > 0
            members[user_id] = {
                'spend': spend,
                'max_budget': default_cap if uses_default_budget else team_max_budget,
                'uses_shared_budget': not uses_default_budget,
            }
            # Creating a private budget creates a zero-spend membership, not a key counter.
            member_counters[user_id] = {
                'source': 'new_membership',
                'spend': 0.0,
                'budget_id': None,
                'effective_budget_id': (
                    default_member_budget_id if uses_default_budget else None
                ),
                'budget_source': 'default_member' if uses_default_budget else 'team',
                'budget_duration': None,
                'budget_reset_at': None,
                'reset_known': True,
            }

        logger.debug(
            'LiteLlmManager:_get_team_members_financial_data:success',
            extra={'team_id': team_id, 'member_count': len(members)},
        )
        financial_data = {
            'team_max_budget': team_max_budget,
            'team_spend': team_spend,
            'members': members,
            'member_counters': member_counters,
            'team_budget_duration': team_data.get('budget_duration'),
            'team_budget_reset_at': team_data.get('budget_reset_at'),
            'team_reset_known': {'budget_duration', 'budget_reset_at'}
            <= team_data.keys(),
            'default_member_budget_id': default_member_budget_id,
            'default_member_budget': default_budget,
        }
        if include_control_policy:
            policy_fields = (
                'max_budget',
                'soft_budget',
                'budget_duration',
                'budget_reset_at',
                'budget_limits',
                'models',
                'allowed_models',
                'model_max_budget',
                'blocked',
                'rpm_limit',
                'tpm_limit',
                'max_parallel_requests',
            )
            keys = team_info.get('keys')
            if not isinstance(keys, list):
                raise ValueError('LiteLLM team response is missing key policies')
            key_policies = []
            for key in keys:
                key_data = LiteLlmManager._member_dict(key)
                token = key_data.get('token')
                if not isinstance(token, str) or not token:
                    raise ValueError('LiteLLM key policy is missing its identity')
                key_policies.append(
                    {
                        'identity': hashlib.sha256(token.encode()).hexdigest(),
                        'user_id': key_data.get('user_id'),
                        **{field: key_data.get(field) for field in policy_fields},
                    }
                )
            financial_data['control_policy'] = {
                'team': {field: team_data.get(field) for field in policy_fields},
                'members': {
                    user_id: {
                        field: (membership.get('litellm_budget_table') or {}).get(field)
                        for field in policy_fields
                    }
                    for user_id, membership in membership_by_user_id.items()
                },
                'keys': sorted(key_policies, key=lambda item: item['identity']),
                'default_member': {
                    field: (default_budget or {}).get(field) for field in policy_fields
                },
            }
        return financial_data

    @staticmethod
    def with_http_client(
        internal_fn: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(internal_fn)
        async def wrapper(*args, **kwargs):
            headers = {'x-goog-api-key': LITE_LLM_API_KEY} if LITE_LLM_API_KEY else {}
            async with httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(30.0),
            ) as client:
                return await internal_fn(client, *args, **kwargs)

        return wrapper

    # Public methods with injected client
    create_team = staticmethod(with_http_client(_create_team))
    get_team = staticmethod(with_http_client(_get_team))
    update_team = staticmethod(with_http_client(_update_team))
    user_exists = staticmethod(with_http_client(_user_exists))
    create_user = staticmethod(with_http_client(_create_user))
    get_user = staticmethod(with_http_client(_get_user))
    update_user = staticmethod(with_http_client(_update_user))
    delete_user = staticmethod(with_http_client(_delete_user))
    delete_team = staticmethod(with_http_client(_delete_team))
    add_user_to_team = staticmethod(with_http_client(_add_user_to_team))
    remove_user_from_team = staticmethod(with_http_client(_remove_user_from_team))
    get_user_team_info = staticmethod(with_http_client(_get_user_team_info))
    apply_budget_write = staticmethod(with_http_client(_apply_budget_write))
    update_user_in_team = staticmethod(with_http_client(_update_user_in_team))
    generate_key = staticmethod(with_http_client(_generate_key))
    get_key_info = staticmethod(with_http_client(_get_key_info))
    verify_existing_key = staticmethod(with_http_client(_verify_existing_key))
    verify_existing_key_strict = staticmethod(
        with_http_client(_verify_existing_key_strict)
    )
    delete_key = staticmethod(with_http_client(_delete_key))
    get_user_keys = staticmethod(with_http_client(_get_user_keys))
    delete_key_by_alias = staticmethod(with_http_client(_delete_key_by_alias))
    delete_key_by_alias_strict = staticmethod(
        with_http_client(_delete_key_by_alias_strict)
    )
    update_user_keys = staticmethod(with_http_client(_update_user_keys))
    get_team_members_financial_data = staticmethod(
        with_http_client(_get_team_members_financial_data)
    )
