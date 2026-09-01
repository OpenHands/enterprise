"""Admin REST API for database-driven feature flags.

All mutation endpoints require the ``MANAGE_FEATURE_FLAGS`` permission, which
is granted only to the ``superadmin`` super role (see
``server.auth.authorization``). Reads of a single flag's enabled state are
also restricted to admins to avoid leaking targeting configuration; callers
that only need an on/off answer for a context should use
``FeatureFlagService.is_enabled`` server-side instead.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from server.auth.authorization import Permission, require_permission
from server.services.feature_flag_service import feature_flag_service
from storage.feature_flag import FeatureFlagRuleEffect
from storage.feature_flag_store import FeatureFlagStore

feature_flag_router = APIRouter(prefix='/api/admin/feature-flags', tags=['Admin'])


class FlagRuleModel(BaseModel):
    """A targeting rule as returned by the API."""

    id: int
    effect: str
    user_id: str | None = None
    org_id: str | None = None
    email_pattern: str | None = None
    percentage: float | None = None
    priority: int = 0


class FlagModel(BaseModel):
    """A feature flag with its rules."""

    key: str
    description: str | None = None
    enabled: bool = False
    rules: list[FlagRuleModel] = Field(default_factory=list)


class CreateFlagRequest(BaseModel):
    key: str = Field(..., description='Unique flag key, e.g. "new_billing_ui".')
    description: str | None = None
    enabled: bool = False


class UpdateFlagRequest(BaseModel):
    description: str | None = None
    enabled: bool | None = None


class CreateRuleRequest(BaseModel):
    effect: FeatureFlagRuleEffect
    user_id: str | None = None
    org_id: str | None = None
    email_pattern: str | None = Field(
        default=None,
        description='SQL LIKE pattern for email matching, e.g. "%@openhands.dev".',
    )
    percentage: float | None = Field(
        default=None, ge=0, le=100, description='0-100 inclusive rollout bucket.'
    )
    priority: int = 0


class EvaluateRequest(BaseModel):
    """Context to evaluate a flag against."""

    user_id: str | None = None
    org_id: str | None = None
    email: str | None = None


class EvaluateResponse(BaseModel):
    enabled: bool


def _rule_to_model(rule) -> FlagRuleModel:
    return FlagRuleModel(
        id=rule.id,
        effect=rule.effect,
        user_id=rule.user_id,
        org_id=rule.org_id,
        email_pattern=rule.email_pattern,
        percentage=rule.percentage,
        priority=rule.priority,
    )


async def _flag_to_model(key: str) -> FlagModel:
    flag = await FeatureFlagStore.get_flag(key)
    if flag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Flag not found'
        )
    rules = await FeatureFlagStore.list_rules(key)
    return FlagModel(
        key=flag.key,
        description=flag.description,
        enabled=flag.enabled,
        rules=[_rule_to_model(r) for r in rules],
    )


@feature_flag_router.get('', response_model=list[FlagModel])
async def list_flags(
    _: str = Depends(require_permission(Permission.MANAGE_FEATURE_FLAGS)),
) -> list[FlagModel]:
    """List all flags with their rules."""
    flags = await FeatureFlagStore.list_flags()
    out: list[FlagModel] = []
    for flag in flags:
        rules = await FeatureFlagStore.list_rules(flag.key)
        out.append(
            FlagModel(
                key=flag.key,
                description=flag.description,
                enabled=flag.enabled,
                rules=[_rule_to_model(r) for r in rules],
            )
        )
    return out


@feature_flag_router.get('/{key}', response_model=FlagModel)
async def get_flag(
    key: str,
    _: str = Depends(require_permission(Permission.MANAGE_FEATURE_FLAGS)),
) -> FlagModel:
    """Get a single flag with its rules."""
    return await _flag_to_model(key)


@feature_flag_router.post(
    '', response_model=FlagModel, status_code=status.HTTP_201_CREATED
)
async def create_flag(
    body: CreateFlagRequest,
    _: str = Depends(require_permission(Permission.MANAGE_FEATURE_FLAGS)),
) -> FlagModel:
    """Create a new flag."""
    try:
        await FeatureFlagStore.create_flag(
            key=body.key, description=body.description, enabled=body.enabled
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    feature_flag_service.invalidate(body.key)
    return await _flag_to_model(body.key)


@feature_flag_router.patch('/{key}', response_model=FlagModel)
async def update_flag(
    key: str,
    body: UpdateFlagRequest,
    _: str = Depends(require_permission(Permission.MANAGE_FEATURE_FLAGS)),
) -> FlagModel:
    """Update a flag's description and/or enabled state."""
    updated = await FeatureFlagStore.update_flag(
        key=key, description=body.description, enabled=body.enabled
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Flag not found'
        )
    feature_flag_service.invalidate(key)
    return await _flag_to_model(key)


@feature_flag_router.delete('/{key}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_flag(
    key: str,
    _: str = Depends(require_permission(Permission.MANAGE_FEATURE_FLAGS)),
) -> None:
    """Delete a flag and all its rules."""
    deleted = await FeatureFlagStore.delete_flag(key)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Flag not found'
        )
    feature_flag_service.invalidate(key)


@feature_flag_router.post(
    '/{key}/rules', response_model=FlagModel, status_code=status.HTTP_201_CREATED
)
async def create_rule(
    key: str,
    body: CreateRuleRequest,
    _: str = Depends(require_permission(Permission.MANAGE_FEATURE_FLAGS)),
) -> FlagModel:
    """Add a targeting rule to a flag."""
    try:
        await FeatureFlagStore.create_rule(
            flag_key=key,
            effect=body.effect,
            user_id=body.user_id,
            org_id=body.org_id,
            email_pattern=body.email_pattern,
            percentage=body.percentage,
            priority=body.priority,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    feature_flag_service.invalidate(key)
    return await _flag_to_model(key)


@feature_flag_router.delete('/{key}/rules/{rule_id}', response_model=FlagModel)
async def delete_rule(
    key: str,
    rule_id: int,
    _: str = Depends(require_permission(Permission.MANAGE_FEATURE_FLAGS)),
) -> FlagModel:
    """Remove a targeting rule from a flag."""
    deleted = await FeatureFlagStore.delete_rule(rule_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Rule not found'
        )
    feature_flag_service.invalidate(key)
    return await _flag_to_model(key)


@feature_flag_router.post('/{key}/evaluate', response_model=EvaluateResponse)
async def evaluate_flag(
    key: str,
    body: EvaluateRequest,
    _: str = Depends(require_permission(Permission.MANAGE_FEATURE_FLAGS)),
) -> EvaluateResponse:
    """Evaluate a flag for a given context (admin preview/testing helper)."""
    enabled = await feature_flag_service.is_enabled(
        key=key,
        user_id=body.user_id,
        org_id=body.org_id,
        email=body.email,
    )
    return EvaluateResponse(enabled=enabled)
