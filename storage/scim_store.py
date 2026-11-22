"""Store for the SCIM users-only prototype (``storage.scim``)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from storage.api_key import ApiKey
from storage.database import a_session_maker
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.scim import ScimCredential, ScimUser
from storage.user import User

SCIM_TOKEN_PREFIX = 'ohscim_'
# The only organization role this prototype grants, adopts or removes.
SCIM_MANAGED_ROLE = 'member'


class ScimUniquenessError(Exception):
    """``userName``, ``externalId`` or the linked account is already taken."""


class ScimDeactivatedError(Exception):
    """The organization's directory has deactivated this person.

    Raised by ``OrgMemberStore.add_user_to_org``: nothing but a SCIM
    reactivation may add them back.
    """


class ScimPrivilegedMemberError(Exception):
    """The account holds a role in the organization that SCIM does not manage."""


def hash_scim_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _uniqueness_error(exc: IntegrityError) -> ScimUniquenessError | None:
    message = str(exc.orig)
    if 'uq_scim_user_org' in message:
        return ScimUniquenessError(message)
    return None


class ScimStore:
    @staticmethod
    async def mint_credential(
        org_id: UUID, auth_provider_id: int
    ) -> tuple[ScimCredential, str]:
        """Create a credential and return it with its token, shown only here."""
        token = SCIM_TOKEN_PREFIX + secrets.token_urlsafe(32)
        async with a_session_maker() as session:
            credential = ScimCredential(
                org_id=org_id,
                auth_provider_id=auth_provider_id,
                token_hash=hash_scim_token(token),
            )
            session.add(credential)
            await session.commit()
            await session.refresh(credential)
            return credential, token

    @staticmethod
    async def get_credential_by_token(token: str) -> ScimCredential | None:
        if not token.startswith(SCIM_TOKEN_PREFIX):
            return None
        async with a_session_maker() as session:
            return await session.scalar(
                select(ScimCredential).where(
                    ScimCredential.token_hash == hash_scim_token(token),
                    ScimCredential.revoked_at.is_(None),
                )
            )

    @staticmethod
    async def revoke_credential(credential_id: UUID) -> None:
        async with a_session_maker() as session:
            credential = await session.get(ScimCredential, credential_id)
            if credential is not None and credential.revoked_at is None:
                credential.revoked_at = datetime.now(timezone.utc)
                await session.commit()

    @staticmethod
    async def is_deactivated(
        org_id: UUID,
        user_id: UUID,
        *,
        session: AsyncSession | None = None,
        lock: bool = False,
    ) -> bool:
        """Whether ``org_id``'s directory has deactivated this account.

        A record linked to an account speaks for that account and no other.
        A record with no link -- never linked, or detached when its account
        was deleted -- speaks for whoever holds its email, so signing up again
        does not get past the marker. ``email`` cannot be changed through SCIM
        once the record exists, which is what makes it safe to match on.

        The marker is ``deactivated_at``, not ``active``. A record that
        ``create`` has inserted but not yet linked is inactive too, and must
        not deny anyone: only a deactivation that went through sets the
        timestamp.

        ``lock`` share-locks the matching records until the caller's
        transaction ends, so a membership insert and a concurrent deactivation
        of the same person cannot both go through unseen by the other.
        """
        account_email = (
            select(func.lower(User.email)).where(User.id == user_id).scalar_subquery()
        )
        query = select(ScimUser.deactivated_at).where(
            ScimUser.org_id == org_id,
            or_(
                ScimUser.user_id == user_id,
                and_(ScimUser.user_id.is_(None), ScimUser.email == account_email),
            ),
        )
        if lock:
            query = query.with_for_update(read=True)
        if session is not None:
            markers = (await session.scalars(query)).all()
        else:
            async with a_session_maker() as own_session:
                markers = (await own_session.scalars(query)).all()
        return any(marker is not None for marker in markers)

    @staticmethod
    async def get_user(org_id: UUID, scim_id: UUID) -> ScimUser | None:
        async with a_session_maker() as session:
            return await session.scalar(
                select(ScimUser).where(
                    ScimUser.id == scim_id, ScimUser.org_id == org_id
                )
            )

    @staticmethod
    async def get_account(user_id: UUID) -> User | None:
        async with a_session_maker() as session:
            return await session.get(User, user_id)

    @staticmethod
    async def membership_is_managed(org_id: UUID, user_id: UUID) -> bool | None:
        """``None`` without a membership; else whether it is a plain member's."""
        async with a_session_maker() as session:
            role_id = await session.scalar(
                select(OrgMember.role_id).where(
                    OrgMember.org_id == org_id, OrgMember.user_id == user_id
                )
            )
            if role_id is None:
                return None
            return role_id == await managed_role_id(session)

    @staticmethod
    async def list_users(
        org_id: UUID,
        *,
        user_name: str | None = None,
        external_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[int, list[ScimUser]]:
        conditions = [ScimUser.org_id == org_id]
        if user_name is not None:
            conditions.append(func.lower(ScimUser.user_name) == user_name.lower())
        if external_id is not None:
            conditions.append(ScimUser.external_id == external_id)
        async with a_session_maker() as session:
            total = await session.scalar(
                select(func.count()).select_from(ScimUser).where(*conditions)
            )
            if limit <= 0:
                return total or 0, []
            rows = await session.scalars(
                select(ScimUser)
                .where(*conditions)
                .order_by(ScimUser.created_at, ScimUser.id)
                .offset(offset)
                .limit(limit)
            )
            return total or 0, list(rows)

    @staticmethod
    async def insert_user(**fields: Any) -> ScimUser:
        async with a_session_maker() as session:
            record = ScimUser(**fields)
            session.add(record)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise _uniqueness_error(exc) or exc from exc
            await session.refresh(record)
            return record

    @staticmethod
    async def update_user(
        org_id: UUID, scim_id: UUID, **fields: Any
    ) -> ScimUser | None:
        async with a_session_maker() as session:
            record = await session.scalar(
                select(ScimUser)
                .where(ScimUser.id == scim_id, ScimUser.org_id == org_id)
                .with_for_update()
            )
            if record is None:
                return None
            for name, value in fields.items():
                setattr(record, name, value)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise _uniqueness_error(exc) or exc from exc
            await session.refresh(record)
            return record

    @staticmethod
    async def delete_user(org_id: UUID, scim_id: UUID) -> None:
        """Drop a record whose provisioning did not complete. Not a SCIM DELETE."""
        async with a_session_maker() as session:
            await session.execute(
                delete(ScimUser).where(
                    ScimUser.id == scim_id, ScimUser.org_id == org_id
                )
            )
            await session.commit()

    @staticmethod
    async def deactivate(
        org_id: UUID, scim_id: UUID, **profile: Any
    ) -> tuple[ScimUser, bool] | None:
        """Mark a record inactive and take away what its account held in the org.

        One transaction: the marker, the membership row, the API keys bound to
        the organization and the account's current-organization pointer change
        together or not at all. ``OrgMemberStore.remove_user_from_org`` does
        the membership and key deletes in a transaction of its own, which
        would leave a window with the marker set and the access still live.

        ``profile`` fields from a ``PUT`` are written in the same transaction,
        so one that collides with another record's ``userName`` or
        ``externalId`` raises ``ScimUniquenessError`` with the membership and
        keys untouched.

        Returns the record and whether a membership was removed, or ``None``
        when the organization has no such record.

        Raises ``ScimPrivilegedMemberError``, changing nothing, when the
        account's membership is anything but a plain member. The role is read
        from the membership row locked in this transaction, not from what it
        was when the record was created, so a promotion made since then is
        seen, and one racing this call either lands first and is refused or
        waits and finds the row gone.
        """
        async with a_session_maker() as session:
            record = await session.scalar(
                select(ScimUser)
                .where(ScimUser.id == scim_id, ScimUser.org_id == org_id)
                .with_for_update()
            )
            if record is None:
                return None
            for name, value in profile.items():
                setattr(record, name, value)
            if record.deactivated_at is None:
                record.active = False
                record.deactivated_at = datetime.now(timezone.utc)
            try:
                await session.flush()
            except IntegrityError as exc:
                await session.rollback()
                raise _uniqueness_error(exc) or exc from exc

            removed = False
            user_id = record.user_id
            if user_id is not None:
                member = await session.scalar(
                    select(OrgMember)
                    .where(OrgMember.org_id == org_id, OrgMember.user_id == user_id)
                    .with_for_update()
                )
                if member is not None:
                    await _refuse_privileged(session, member)
                    await session.delete(member)
                    removed = True
                await session.execute(
                    delete(ApiKey).where(
                        ApiKey.user_id == str(user_id), ApiKey.org_id == org_id
                    )
                )
                await _leave_current_org(session, org_id, user_id)
            await session.commit()
            await session.refresh(record)
            return record, removed


async def managed_role_id(session: AsyncSession) -> int | None:
    return await session.scalar(select(Role.id).where(Role.name == SCIM_MANAGED_ROLE))


async def _refuse_privileged(session: AsyncSession, member: OrgMember) -> None:
    if member.role_id != await managed_role_id(session):
        raise ScimPrivilegedMemberError(str(member.org_id))


async def _leave_current_org(
    session: AsyncSession, org_id: UUID, user_id: UUID
) -> None:
    """Point the account away from ``org_id`` if that is its current organization.

    ``SaasUserAuth`` falls back to ``user.current_org_id`` without looking for
    a membership, so a pointer left on the organization would still resolve.
    Prefers the personal workspace, as ``remove_member_with_cleanup`` does.
    """
    user = await session.get(User, user_id)
    if user is None or user.current_org_id != org_id:
        return
    fallback = await session.scalar(select(Org.id).where(Org.id == user_id))
    if fallback is None:
        fallback = await session.scalar(
            select(OrgMember.org_id)
            .where(OrgMember.user_id == user_id, OrgMember.org_id != org_id)
            .order_by(OrgMember.org_id)
            .limit(1)
        )
    if fallback is not None:
        user.current_org_id = fallback
