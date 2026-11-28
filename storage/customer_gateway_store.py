"""Store for the opt-in customer gateway. The only reads are masked metadata and
the relay's per-request resolution; no API or CLI path returns a customer key."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert

from storage.customer_gateway import (
    CustomerGatewayMemberCredential,
    CustomerGatewayOrgConfig,
)
from storage.database import a_session_maker
from storage.encrypt_utils import decrypt_value, encrypt_value
from storage.org_member import OrgMember
from storage.user import ACCOUNT_TYPE_HUMAN, User

audit_logger = logging.getLogger('customer_gateway.audit')


class CustomerGatewayDenied(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class OrgGateway:
    org_id: UUID
    gateway_id: str
    profile: dict[str, Any]
    enabled_since: datetime | None = None


@dataclass(frozen=True)
class MemberResolution:
    upstream_key: str = field(repr=False)
    fingerprint: str
    credential_version: int
    policy_version: int
    allowed_models: frozenset[str]


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _audit(event: str, actor: str, **fields: Any) -> None:
    audit_logger.info(
        'customer_gateway_audit',
        extra={
            'event': event,
            'actor': actor,
            **{k: str(v) for k, v in fields.items()},
        },
    )


def _require_active_user(is_disabled: bool, deletion_pending: bool) -> None:
    # Same user conditions as server.auth.saas_user_auth._require_active_user, checked on
    # every mint and every relay request, so a disable takes effect before token expiry.
    if is_disabled or deletion_pending:
        raise CustomerGatewayDenied('user_inactive')


class CustomerGatewayStore:
    @staticmethod
    async def get_org_gateway(org_id: UUID) -> OrgGateway | None:
        """The org's stored config regardless of ``enabled``, for operator tooling
        (e.g. validating ``set-key --models`` against the catalog) that must work
        whether or not the gateway is currently on."""
        async with a_session_maker() as session:
            row = await session.get(CustomerGatewayOrgConfig, org_id)
        if row is None:
            return None
        return OrgGateway(
            row.org_id, row.gateway_id, dict(row.profile), row.enabled_since
        )

    @staticmethod
    async def get_enabled_org_gateway(org_id: UUID) -> OrgGateway | None:
        async with a_session_maker() as session:
            row = await session.get(CustomerGatewayOrgConfig, org_id)
        if row is None or not row.enabled:
            return None
        return OrgGateway(
            row.org_id, row.gateway_id, dict(row.profile), row.enabled_since
        )

    @staticmethod
    async def set_org_gateway(
        org_id: UUID,
        gateway_id: str,
        profile: dict[str, Any],
        enabled: bool,
        actor: str,
    ) -> None:
        values = dict(
            org_id=org_id,
            gateway_id=gateway_id,
            profile=profile,
            enabled=enabled,
            updated_by=actor,
        )
        table = CustomerGatewayOrgConfig
        stmt = insert(table).values(
            **values, enabled_since=func.now() if enabled else None
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=['org_id'],
            set_={
                **{k: v for k, v in values.items() if k != 'org_id'},
                'updated_at': func.now(),
                'enabled_since': case(
                    (~stmt.excluded.enabled, None),
                    (~table.enabled, func.now()),
                    else_=table.enabled_since,
                ),
            },
        )
        async with a_session_maker() as session:
            await session.execute(stmt)
            await session.commit()
        _audit(
            'org_gateway_set',
            actor,
            org_id=org_id,
            gateway_id=gateway_id,
            enabled=enabled,
        )

    @staticmethod
    async def set_member_credential(
        org_id: UUID, user_id: UUID, key: str, allowed_models: list[str], actor: str
    ) -> int:
        """Insert or atomically rotate one member's key; identity stays (org_id, user_id)."""
        if not key.strip():
            raise ValueError('empty key')
        async with a_session_maker() as session:
            user = (
                await session.execute(
                    select(
                        User.account_type,
                        User.is_disabled,
                        User.deletion_pending,
                        OrgMember.user_id.label('member_id'),
                    )
                    .outerjoin(
                        OrgMember,
                        (OrgMember.user_id == User.id) & (OrgMember.org_id == org_id),
                    )
                    .where(User.id == user_id)
                )
            ).first()
        # Explicit, operator-readable denial; the composite FK to org_member still
        # enforces membership at insert time.
        if user is None or user.member_id is None:
            raise CustomerGatewayDenied('not_a_member')
        if user.account_type != ACCOUNT_TYPE_HUMAN:
            raise CustomerGatewayDenied('service_account')
        _require_active_user(user.is_disabled, user.deletion_pending)
        fp = fingerprint(key)
        table = CustomerGatewayMemberCredential
        stmt = insert(table).values(
            org_id=org_id,
            user_id=user_id,
            ciphertext=encrypt_value(key),
            fingerprint=fp,
            allowed_models=sorted(set(allowed_models)),
            updated_by=actor,
        )
        upsert = stmt.on_conflict_do_update(
            index_elements=['org_id', 'user_id'],
            set_={
                'ciphertext': stmt.excluded.ciphertext,
                'fingerprint': stmt.excluded.fingerprint,
                'allowed_models': stmt.excluded.allowed_models,
                'credential_version': table.credential_version + 1,
                'updated_by': actor,
                'updated_at': func.now(),
            },
        ).returning(table.credential_version)
        async with a_session_maker() as session:
            version = (await session.execute(upsert)).scalar_one()
            await session.commit()
        _audit(
            'member_credential_set',
            actor,
            org_id=org_id,
            user_id=user_id,
            fingerprint=fp,
            credential_version=version,
        )
        return version

    @staticmethod
    async def set_member_enabled(
        org_id: UUID, user_id: UUID, enabled: bool, actor: str
    ) -> int:
        """Disabling also bumps policy_version so every outstanding inference token dies."""
        table = CustomerGatewayMemberCredential
        stmt = (
            update(table)
            .where(table.org_id == org_id, table.user_id == user_id)
            .values(
                enabled=enabled,
                policy_version=table.policy_version + 1,
                updated_by=actor,
                updated_at=func.now(),
            )
            .returning(table.policy_version)
        )
        async with a_session_maker() as session:
            version = (await session.execute(stmt)).scalar_one_or_none()
            await session.commit()
        if version is None:
            raise CustomerGatewayDenied('no_credential')
        _audit(
            'member_enabled_set',
            actor,
            org_id=org_id,
            user_id=user_id,
            enabled=enabled,
            policy_version=version,
        )
        return version

    @staticmethod
    async def masked_status(org_id: UUID) -> list[dict[str, Any]]:
        table = CustomerGatewayMemberCredential
        async with a_session_maker() as session:
            rows = await session.execute(
                select(
                    table.user_id,
                    table.fingerprint,
                    table.credential_version,
                    table.policy_version,
                    table.allowed_models,
                    table.enabled,
                ).where(table.org_id == org_id)
            )
            return [dict(r._mapping) for r in rows]

    @staticmethod
    async def current_policy_version(org_id: UUID, user_id: UUID) -> int:
        """For token minting: requires an active member with an enabled credential."""
        res = await CustomerGatewayStore._load(org_id, user_id)
        return res.policy_version

    @staticmethod
    async def current_allowed_models(org_id: UUID, user_id: UUID) -> frozenset[str]:
        """The member's granted aliases under the same checks as token minting."""
        res = await CustomerGatewayStore._load(org_id, user_id)
        return res.allowed_models

    @staticmethod
    async def resolve(
        org_id: UUID, user_id: UUID, policy_version: int
    ) -> MemberResolution:
        res = await CustomerGatewayStore._load(org_id, user_id)
        if res.policy_version != policy_version:
            raise CustomerGatewayDenied('token_revoked')
        return res

    @staticmethod
    async def _load(org_id: UUID, user_id: UUID) -> MemberResolution:
        table = CustomerGatewayMemberCredential
        async with a_session_maker() as session:
            row = (
                await session.execute(
                    select(
                        table,
                        OrgMember.status,
                        User.account_type,
                        User.is_disabled,
                        User.deletion_pending,
                    )
                    .join(
                        OrgMember,
                        (OrgMember.org_id == table.org_id)
                        & (OrgMember.user_id == table.user_id),
                    )
                    .join(User, User.id == table.user_id)
                    .where(table.org_id == org_id, table.user_id == user_id)
                )
            ).first()
        if row is None:
            raise CustomerGatewayDenied('no_credential')
        cred, member_status, account_type, is_disabled, deletion_pending = row
        # Principal from the user row, never from a caller-supplied launch trigger.
        if account_type != ACCOUNT_TYPE_HUMAN:
            raise CustomerGatewayDenied('service_account')
        _require_active_user(is_disabled, deletion_pending)
        # Native states: active, inactive, invited, disabled. NULL is legacy active:
        # OrgMemberStore.add_user_to_org defaults status to None for real members.
        if member_status not in ('active', None):
            raise CustomerGatewayDenied('membership_inactive')
        if not cred.enabled:
            raise CustomerGatewayDenied('credential_disabled')
        return MemberResolution(
            upstream_key=decrypt_value(cred.ciphertext),
            fingerprint=cred.fingerprint,
            credential_version=cred.credential_version,
            policy_version=cred.policy_version,
            allowed_models=frozenset(cred.allowed_models),
        )
