"""Claim durable work, call external services after commit, then revalidate."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.utils.litellm_integration import is_litellm_enabled
from server.auth.native_types import SessionFactory
from server.services.native_account_service import lock_native_lifecycle
from storage.database import a_session_maker
from storage.native_auth import AuthAccount
from storage.native_external_work import NativeExternalWork
from storage.org import Org
from storage.org_member import MANAGED_LLM_KEY_OWNERSHIP_VERSION, OrgMember
from storage.user import User


@dataclass(frozen=True)
class NativeProvisioningRequest:
    account_id: UUID
    org_id: UUID
    email: str
    work_id: UUID
    member_key: SecretStr = field(repr=False)

    @property
    def idempotency_key(self) -> str:
        return f'native-member:{self.work_id}'


@dataclass(frozen=True)
class NativeProvisionedKeys:
    member_key: SecretStr


async def queue_external_cleanup(
    session: AsyncSession, *, account_id: UUID | None = None, org_id: UUID | None = None
) -> None:
    """Call while holding the native lifecycle lock in the deleting transaction."""
    predicate = (
        (NativeExternalWork.org_id == org_id)
        if org_id
        else (NativeExternalWork.account_id == account_id)
    )
    works = list(
        await session.scalars(
            select(NativeExternalWork).where(
                predicate, NativeExternalWork.kind == 'provision'
            )
        )
    )
    for work in works:
        if work.payload:
            work.status = 'cleanup'
    if not is_litellm_enabled() and not any(work.payload for work in works):
        return
    kind = 'delete_team' if org_id else 'delete_user'
    exists = await session.scalar(
        select(NativeExternalWork.id).where(
            predicate,
            NativeExternalWork.kind == kind,
            NativeExternalWork.status != 'complete',
        )
    )
    if exists is None:
        session.add(
            NativeExternalWork(
                account_id=account_id, org_id=org_id, kind=kind, payload={}
            )
        )


class NativeProvisioningService:
    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self.sessions = session_factory or a_session_maker

    async def _suspend_disabled_work(self) -> None:
        """Revoke local managed keys while preserving deferred remote cleanup.

        Detach memberships under the same lock used to publish remote results,
        so an in-flight worker cannot restore their managed keys. Keep encrypted
        key payloads and leases until the integration is re-enabled: disabling a
        gateway does not mean its remote resources were deleted. Terminal local
        account deletion has its own cleanup_pending state and still runs.
        """
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            works = list(
                await session.scalars(
                    select(NativeExternalWork).where(
                        or_(
                            NativeExternalWork.status != 'complete',
                            NativeExternalWork.id.in_(
                                select(OrgMember.native_provisioning_id).where(
                                    OrgMember.native_provisioning_id.is_not(None)
                                )
                            ),
                        )
                    )
                )
            )
            for work in works:
                if work.kind == 'provision' and work.payload:
                    work.status = 'cleanup'
            members = await session.scalars(
                select(OrgMember).where(
                    or_(
                        OrgMember.native_provisioning_id.is_not(None),
                        OrgMember.status == 'pending_llm_provisioning',
                    )
                )
            )
            for member in members:
                if not member.has_custom_llm_api_key:
                    member.llm_api_key = SecretStr('')
                    member.managed_llm_key_ownership_version = 0
                member.native_provisioning_id = None
                member.status = 'active'
            await session.execute(
                update(AuthAccount)
                .where(AuthAccount.provisioning_status == 'pending')
                .values(provisioning_status='complete')
            )

    async def _eligible(
        self, session: AsyncSession, work: NativeExternalWork
    ) -> tuple[AuthAccount | None, OrgMember | None, bool]:
        account = await session.get(AuthAccount, work.account_id)
        user = await session.get(User, work.account_id)
        member = await session.get(OrgMember, (work.org_id, work.account_id))
        org = await session.get(Org, work.org_id)
        return (
            account,
            member,
            account is not None
            and account.state == 'profile_present'
            and user is not None
            and not user.is_disabled
            and org is not None
            and member is not None
            and not member.has_custom_llm_api_key
            and member.native_provisioning_id == work.id
            and member.status == 'pending_llm_provisioning',
        )

    async def reconcile(
        self,
        provision: Callable[
            [NativeProvisioningRequest], Awaitable[NativeProvisionedKeys]
        ]
        | None = None,
        *,
        limit: int = 100,
        only_work_id: UUID | None = None,
    ) -> tuple[int, int]:
        if not is_litellm_enabled():
            await self._suspend_disabled_work()
            return 0, 0
        if provision is None:
            from server.services.native_litellm_adapter import provision_native_member

            provision = provision_native_member
        now = datetime.now(UTC)
        async with self.sessions() as session:
            work_ids = list(
                await session.scalars(
                    select(NativeExternalWork.id)
                    .join(User, User.id == NativeExternalWork.account_id)
                    .join(AuthAccount, AuthAccount.id == User.id)
                    .where(
                        NativeExternalWork.kind == 'provision',
                        User.is_disabled.is_(False),
                        AuthAccount.state == 'profile_present',
                        NativeExternalWork.id == only_work_id
                        if only_work_id
                        else true(),
                        NativeExternalWork.status.in_(('pending', 'running')),
                        or_(
                            NativeExternalWork.lease_until.is_(None),
                            NativeExternalWork.lease_until < now,
                        ),
                    )
                    .order_by(NativeExternalWork.created_at)
                    .limit(limit)
                )
            )
        completed = failed = 0
        for work_id in work_ids:
            claim_id = uuid4()
            try:
                async with self.sessions() as session, session.begin():
                    await lock_native_lifecycle(session)
                    work = await session.get_one(
                        NativeExternalWork, work_id, with_for_update=True
                    )
                    if work.status not in ('pending', 'running') or (
                        work.lease_until and work.lease_until > datetime.now(UTC)
                    ):
                        continue
                    account, member, eligible = await self._eligible(session, work)
                    if not eligible or account is None or member is None:
                        # Disabled accounts keep their pending provisioning, but
                        # any in-flight generated key must be removed first.
                        work.status = 'cleanup'
                        continue
                    if account.display_email is None:
                        work.status = 'cleanup'
                        continue
                    if work.account_id is None or work.org_id is None:
                        work.status = 'cleanup'
                        continue
                    security_version = account.session_version
                    work.claim_id = claim_id
                    work.lease_until = datetime.now(UTC) + timedelta(minutes=2)
                    work.status = 'running'
                    request = NativeProvisioningRequest(
                        work.account_id,
                        work.org_id,
                        account.display_email,
                        work.id,
                        SecretStr(work.payload['member_key']),
                    )
                keys = await asyncio.wait_for(provision(request), timeout=30)
                if not keys.member_key.get_secret_value():
                    raise ValueError('Provisioning returned no usable member key')
                async with self.sessions() as session, session.begin():
                    await lock_native_lifecycle(session)
                    work = await session.get_one(
                        NativeExternalWork, work_id, with_for_update=True
                    )
                    account, member, eligible = await self._eligible(session, work)
                    if work.claim_id != claim_id:
                        continue
                    work.lease_until = None
                    if (
                        not eligible
                        or account is None
                        or member is None
                        or account.session_version != security_version
                        or work.status != 'running'
                    ):
                        work.status = 'cleanup'
                        continue
                    member.llm_api_key = keys.member_key
                    member.managed_llm_key_ownership_version = (
                        MANAGED_LLM_KEY_OWNERSHIP_VERSION
                    )
                    member.status = 'active'
                    work.status = 'complete'
                    await session.flush()
                    pending = await session.scalar(
                        select(func.count())
                        .select_from(OrgMember)
                        .where(
                            OrgMember.user_id == work.account_id,
                            OrgMember.status == 'pending_llm_provisioning',
                        )
                    )
                    if not pending:
                        account.provisioning_status = 'complete'
                completed += 1
            except Exception:
                # Provider errors may carry credentials; report only counts.
                # The lease makes retries safe after process loss/timeouts.
                failed += 1
                async with self.sessions() as session, session.begin():
                    await lock_native_lifecycle(session)
                    retry_work = await session.get(NativeExternalWork, work_id)
                    if (
                        retry_work is not None
                        and retry_work.claim_id == claim_id
                        and retry_work.status == 'running'
                    ):
                        retry_work.status = 'pending'
                        retry_work.lease_until = None
        return completed, failed

    async def cleanup(self, *, limit: int = 100) -> tuple[int, int]:
        if not is_litellm_enabled():
            await self._suspend_disabled_work()
            return 0, 0
        from server.services.native_litellm_adapter import cleanup_native_resource

        async with self.sessions() as session:
            ids = list(
                await session.scalars(
                    select(NativeExternalWork.id)
                    .where(
                        or_(
                            NativeExternalWork.kind != 'provision',
                            NativeExternalWork.status == 'cleanup',
                        ),
                        NativeExternalWork.status != 'complete',
                    )
                    .order_by(NativeExternalWork.created_at)
                    .limit(limit)
                )
            )
        complete = failed = 0
        for work_id in ids:
            claim = uuid4()
            try:
                async with self.sessions() as session, session.begin():
                    await lock_native_lifecycle(session)
                    work = await session.get_one(NativeExternalWork, work_id)
                    if work.status == 'complete' or (
                        work.lease_until and work.lease_until > datetime.now(UTC)
                    ):
                        continue
                    predicate = (
                        NativeExternalWork.org_id == work.org_id
                        if work.org_id
                        else NativeExternalWork.account_id == work.account_id
                    )
                    leased = await session.scalar(
                        select(NativeExternalWork.id)
                        .where(
                            predicate,
                            NativeExternalWork.id != work.id,
                            NativeExternalWork.lease_until > datetime.now(UTC),
                        )
                        .limit(1)
                    )
                    if leased is not None:
                        continue
                    work.claim_id = claim
                    work.lease_until = datetime.now(UTC) + timedelta(minutes=2)
                    kind, account_id, org_id, payload = (
                        work.kind,
                        work.account_id,
                        work.org_id,
                        work.payload,
                    )
                await asyncio.wait_for(
                    cleanup_native_resource(kind, account_id, org_id, payload),
                    timeout=30,
                )
                async with self.sessions() as session, session.begin():
                    await lock_native_lifecycle(session)
                    work = await session.get_one(NativeExternalWork, work_id)
                    if work.claim_id != claim:
                        continue
                    work.lease_until = None
                    work.status = 'complete'
                    # Disabled/pending profiles can retry this exact key after
                    # cleanup. Deleted membership generations never revive.
                    if kind == 'provision':
                        account, member, eligible = await self._eligible(session, work)
                        if eligible:
                            work.status = 'pending'
                        elif (
                            member is not None
                            and member.native_provisioning_id == work.id
                            and member.status == 'pending_llm_provisioning'
                            and account is not None
                            and account.state == 'profile_present'
                        ):
                            work.status = 'suspended'
                    if work.status == 'complete':
                        work.payload = {}
                complete += 1
            except Exception:
                failed += 1
                async with self.sessions() as session, session.begin():
                    work = await session.get_one(
                        NativeExternalWork, work_id, with_for_update=True
                    )
                    if work.claim_id == claim:
                        work.lease_until = None
        return complete, failed


async def prepare_managed_member(
    session: AsyncSession, member: OrgMember, *, force: bool = False
) -> SecretStr:
    """Select or queue a member key; caller holds the native lifecycle lock."""
    if not is_litellm_enabled():
        await release_managed_member(session, member)
        if not member.has_custom_llm_api_key:
            member.llm_api_key = SecretStr('')
            member.managed_llm_key_ownership_version = 0
        return member.llm_api_key or SecretStr('')
    import secrets

    work = (
        await session.get(NativeExternalWork, member.native_provisioning_id)
        if member.native_provisioning_id
        else None
    )
    if not force and not member.has_custom_llm_api_key and work is not None:
        if work.status == 'complete' and member.status == 'active':
            return member.llm_api_key
        if work.status in ('pending', 'running', 'cleanup', 'suspended'):
            if work.status == 'suspended':
                work.status = 'pending'
            return SecretStr('')
    if work is not None and work.payload:
        work.status = 'cleanup'
    work_id = uuid4()
    session.add(
        NativeExternalWork(
            id=work_id,
            account_id=member.user_id,
            org_id=member.org_id,
            kind='provision',
            payload={'member_key': 'sk-' + secrets.token_hex(32)},
        )
    )
    member.native_provisioning_id = work_id
    member.llm_api_key = SecretStr('')
    member.has_custom_llm_api_key = False
    member.status = 'pending_llm_provisioning'
    member.managed_llm_key_ownership_version = 0
    account = await session.get_one(AuthAccount, member.user_id)
    account.provisioning_status = 'pending'
    return SecretStr('')


async def release_managed_member(session: AsyncSession, member: OrgMember) -> None:
    """Leave managed provisioning without losing cleanup of an in-flight key."""
    if member.native_provisioning_id:
        work = await session.get(NativeExternalWork, member.native_provisioning_id)
        if work is not None and work.payload:
            work.status = 'cleanup'
    member.native_provisioning_id = None
    member.status = 'active'
