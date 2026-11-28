"""Reconcile an organization's SCIM directory records with accounts and memberships.

The rules, each deliberately narrow:

* SCIM grants one thing: the ``member`` role in the credential's organization.
  It never sets a password, accepts the terms of service, verifies an email,
  mints an API key or links a sign-in subject -- which account a corporate
  login resolves to stays with ``server.auth.provider_identity``.
* A record is linked to an existing account only by email, only when exactly
  one account has that email, and only when the credential's login provider
  has ``allow_match_by_email`` set -- the same trust statement that login
  uses. Anything else is a ``409``; SCIM never creates a second account for an
  email that is already taken.
* A record's email is fixed when it is created. ``PUT`` may change the other
  profile fields, never the email, so an edit in the directory cannot re-point
  a record, or its deactivation, at another account.
* Service accounts, disabled or deletion-pending accounts and holders of an
  instance super role are never linked or reactivated.
* SCIM manages plain members only. An account whose membership in the
  organization is ``owner``, ``admin`` or a service account's is never
  adopted and never removed: both are a ``409`` that changes nothing. The role
  is read when the change is made, so this also covers someone promoted after
  their record was created -- demote or remove them in the app first.
* An account that is already a plain member through an invitation, a group
  mapping or an admin is adopted as it is, and from then on is treated like
  any other record: deactivation removes that membership too.
* Reactivation restores the same account as a plain ``member``. The deleted
  API keys stay deleted.
* A membership removed by hand while the record is active is not re-added by
  a later ``PUT``/``PATCH`` that leaves ``active`` true: only the
  inactive-to-active transition grants.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.provider_identity import add_org_member
from server.constants import ROLE_MEMBER
from server.licensing.limits import UserLimitReachedError
from server.licensing.sso import is_sso_login
from storage.auth_provider import AuthProvider
from storage.lite_llm_manager import LiteLlmManager
from storage.role_store import RoleStore
from storage.scim import (
    MEMBERSHIP_ORIGIN_PREEXISTING,
    MEMBERSHIP_ORIGIN_SCIM,
    ScimUser,
)
from storage.scim_store import (
    ScimPrivilegedMemberError,
    ScimStore,
    ScimUniquenessError,
)
from storage.user import ACCOUNT_TYPE_HUMAN, User
from storage.user_store import UserStore


class ScimError(Exception):
    def __init__(self, status: int, detail: str, scim_type: str | None = None):
        self.status = status
        self.detail = detail
        self.scim_type = scim_type
        super().__init__(detail)


@dataclass(frozen=True)
class ScimUserAttributes:
    user_name: str
    email: str
    active: bool
    external_id: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    display_name: str | None = None

    def profile(self) -> dict[str, str | None]:
        return {
            'user_name': self.user_name,
            'email': self.email,
            'external_id': self.external_id,
            'given_name': self.given_name,
            'family_name': self.family_name,
            'display_name': self.display_name,
        }


def _uniqueness() -> ScimError:
    return ScimError(
        409, 'A user with this userName, externalId or account exists', 'uniqueness'
    )


def _not_found() -> ScimError:
    return ScimError(404, 'User not found')


def _privileged() -> ScimError:
    return ScimError(
        409,
        'This account is an owner, admin or service account of the '
        'organization, which SCIM does not manage',
    )


def is_scim_provider(provider: AuthProvider) -> bool:
    """Whether SCIM may be bound to ``provider``: an enabled corporate SSO login.

    The password IDP, git providers and Keycloak's own rows are not: their
    emails are not a directory's statement about who works where.
    """
    return is_sso_login(
        is_idp=provider.is_idp, enabled=provider.enabled, details=provider.details
    )


class ScimUserService:
    def __init__(self, org_id: uuid.UUID, provider: AuthProvider):
        self.org_id = org_id
        self.provider = provider

    async def create(self, attributes: ScimUserAttributes) -> ScimUser:
        # The record goes in first, inactive and without a deactivation
        # marker: the unique indexes decide a concurrent duplicate before
        # either request can create an account, and until it is linked or
        # deliberately deactivated the row denies nobody.
        try:
            record = await ScimStore.insert_user(
                org_id=self.org_id, active=False, **attributes.profile()
            )
        except ScimUniquenessError as exc:
            raise _uniqueness() from exc
        try:
            if attributes.active:
                return await self._activate(record)
            return await self._deactivate(record)
        except Exception:
            await ScimStore.delete_user(self.org_id, record.id)
            raise

    async def replace(
        self, scim_id: uuid.UUID, attributes: ScimUserAttributes
    ) -> ScimUser:
        record = await self._get(scim_id)
        if attributes.email != record.email:
            # The email is what the record was linked by. Changing it would
            # let a directory edit point a record at someone else's account.
            raise ScimError(
                400, 'The email of an existing user cannot be changed', 'mutability'
            )
        if not attributes.active:
            return await self._deactivate(record, **attributes.profile())
        try:
            updated = await ScimStore.update_user(
                self.org_id, scim_id, **attributes.profile()
            )
        except ScimUniquenessError as exc:
            raise _uniqueness() from exc
        if updated is None:
            raise _not_found()
        if not updated.active:
            return await self._activate(updated)
        return updated

    async def set_active(self, scim_id: uuid.UUID, active: bool) -> ScimUser:
        record = await self._get(scim_id)
        if not active:
            return await self._deactivate(record)
        if record.active:
            return record
        return await self._activate(record)

    async def _get(self, scim_id: uuid.UUID) -> ScimUser:
        record = await ScimStore.get_user(self.org_id, scim_id)
        if record is None:
            raise _not_found()
        return record

    async def _deactivate(self, record: ScimUser, **profile) -> ScimUser:
        if record.user_id is None and record.deactivated_at is None:
            # Never linked and not yet a marker. Whoever holds the email has
            # to pass the same checks as on activation before this record may
            # speak for them; with nobody holding it, it becomes a marker for
            # the email.
            account = await self._match_account(record.email)
            if account is not None:
                record = await self._link(record, account.id)
        try:
            result = await ScimStore.deactivate(self.org_id, record.id, **profile)
        except ScimPrivilegedMemberError as exc:
            raise _privileged() from exc
        except ScimUniquenessError as exc:
            raise _uniqueness() from exc
        if result is None:
            raise _not_found()
        record, membership_removed = result
        logger.info(
            'scim:user_deactivated',
            extra={
                'org_id': str(self.org_id),
                'scim_id': str(record.id),
                'user_id': str(record.user_id) if record.user_id else None,
                'membership_removed': membership_removed,
            },
        )
        if membership_removed and record.user_id is not None:
            # As in ``OrgMemberService.remove_member_with_cleanup``: the access
            # is already gone, LiteLLM follows when it can.
            try:
                await LiteLlmManager.remove_user_from_team(
                    str(record.user_id), str(self.org_id)
                )
            except Exception:
                logger.warning(
                    'scim:litellm_team_removal_failed',
                    extra={'org_id': str(self.org_id), 'scim_id': str(record.id)},
                )
        return record

    async def _activate(self, record: ScimUser) -> ScimUser:
        account = await self._resolve_account(record)
        managed = await ScimStore.membership_is_managed(self.org_id, account.id)
        if managed is False:
            raise _privileged()
        member = managed is True
        origin = record.membership_origin or (
            MEMBERSHIP_ORIGIN_PREEXISTING if member else MEMBERSHIP_ORIGIN_SCIM
        )
        # Lifting the marker comes first, since the re-grant guard in
        # ``OrgMemberStore.add_user_to_org`` reads it; a grant that then fails
        # puts it back.
        record = await self._link(
            record,
            account.id,
            active=True,
            deactivated_at=None,
            membership_origin=origin,
        )
        if not member:
            try:
                await self._grant_membership(account)
            except Exception:
                # A reactivation racing this one may have made the grant.
                if not await ScimStore.membership_is_managed(self.org_id, account.id):
                    await ScimStore.deactivate(self.org_id, record.id)
                    raise
        logger.info(
            'scim:user_activated',
            extra={
                'org_id': str(self.org_id),
                'scim_id': str(record.id),
                'user_id': str(account.id),
                'membership_granted': not member,
            },
        )
        return record

    async def _link(self, record: ScimUser, user_id: uuid.UUID, **fields) -> ScimUser:
        try:
            updated = await ScimStore.update_user(
                self.org_id, record.id, user_id=user_id, **fields
            )
        except ScimUniquenessError as exc:
            raise _uniqueness() from exc
        if updated is None:
            raise _not_found()
        return updated

    async def _resolve_account(self, record: ScimUser) -> User:
        if record.user_id is not None:
            account = await ScimStore.get_account(record.user_id)
            if account is not None:
                _require_eligible(account)
                return account
        account = await self._match_account(record.email)
        if account is not None:
            return account
        return await self._create_account(record)

    async def _match_account(self, email: str) -> User | None:
        count = await UserStore.count_users_with_email(email)
        if count == 0:
            return None
        if count > 1:
            raise ScimError(
                409, 'Multiple accounts share this email; manual resolution required'
            )
        if not self.provider.allow_match_by_email:
            raise ScimError(
                409,
                'An account with this email exists and the login provider does '
                'not allow matching by email',
            )
        account = await UserStore.get_user_by_email(email)
        if account is None:
            return None
        _require_eligible(account)
        return account

    async def _create_account(self, record: ScimUser) -> User:
        if not self.provider.allow_match_by_email:
            # Sign-in could never reach the account: with matching off, the
            # person's first login would register a second one.
            raise ScimError(
                409,
                'The login provider does not allow matching by email, so a '
                'provisioned account could not be signed in to',
            )
        user_info = {
            'email': record.email,
            'email_verified': False,
            'preferred_username': record.email,
            'name': record.display_name or '',
            'given_name': record.given_name or '',
            'family_name': record.family_name or '',
        }
        try:
            account = await UserStore.create_user(
                str(uuid.uuid4()), user_info, suppress_auto_superadmin=True
            )
        except UserLimitReachedError as exc:
            raise ScimError(403, str(exc)) from exc
        if account is None:
            raise ScimError(500, 'Failed to create the account')
        return account

    async def _grant_membership(self, account: User) -> None:
        role = await RoleStore.get_role_by_name(ROLE_MEMBER)
        if role is None:
            raise ScimError(500, 'Member role is not configured')
        if not await add_org_member(self.org_id, account.id, role.id):
            raise ScimError(409, 'The organization cannot take members')
        if account.current_org_id == account.id:
            await UserStore.update_current_org(str(account.id), self.org_id)


def _require_eligible(account: User) -> None:
    if (
        account.account_type != ACCOUNT_TYPE_HUMAN
        or account.is_disabled
        or account.deletion_pending
        or account.role_id is not None
    ):
        raise ScimError(
            409, 'The account with this email cannot be managed through SCIM'
        )
