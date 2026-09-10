"""Purpose-bound action tokens; token consumption commits with its state change."""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from server.auth.contracts import AuthActionEmail as ActionEmail
from server.auth.contracts import InvalidCredentials, IssuedSession
from server.auth.local.accounts import (
    AccountConflict,
    create_local_account,
    validated_email,
)
from server.auth.local.passwords import hash_password
from server.auth.local.sessions import (
    issue_in_transaction,
    lock_account,
    revoke_all_in_transaction,
    token_digest,
)
from server.auth.mode import SessionFactory
from storage.auth_action_tokens import AuthActionToken
from storage.database import a_session_maker
from storage.local_credentials import LocalCredentials, normalize_login_email
from storage.org_invitation import OrgInvitation
from storage.org_member import OrgMember


class InvalidActionToken(ValueError):
    """An action token is invalid, expired, consumed, or no longer applicable."""


class LocalAccountActions:
    def __init__(self, session_factory: SessionFactory | None = None):
        self.session_factory = session_factory or a_session_maker

    async def _issue(
        self, session, user_id: UUID, email: str, purpose: str
    ) -> ActionEmail:
        now = datetime.now(UTC)
        await session.execute(
            delete(AuthActionToken).where(
                AuthActionToken.user_id == user_id,
                AuthActionToken.purpose == purpose,
            )
        )
        token = SecretStr(secrets.token_urlsafe(32))
        session.add(
            AuthActionToken(
                token_digest=token_digest(token),
                user_id=user_id,
                email=email,
                purpose=purpose,
                created_at=now,
                expires_at=now
                + timedelta(hours=1 if purpose == 'password_reset' else 24),
            )
        )
        await session.flush()
        return ActionEmail(email, purpose, token)

    async def request_reset(self, email: str) -> ActionEmail | None:
        async with self.session_factory() as session, session.begin():
            user_id = await session.scalar(
                select(LocalCredentials.user_id).where(
                    LocalCredentials.normalized_email == normalize_login_email(email)
                )
            )
            if user_id is None:
                return None
            try:
                _, credential = await lock_account(session, user_id)
            except InvalidCredentials:
                return None
            return await self._issue(
                session, user_id, credential.normalized_email, 'password_reset'
            )

    async def request_verification(
        self, user_id: UUID, email: str | None = None
    ) -> ActionEmail | None:
        async with self.session_factory() as session, session.begin():
            user, credential = await lock_account(session, user_id)
            if credential.must_change_password:
                raise InvalidCredentials('Change your password first.')
            target = (
                validated_email(email)
                if email is not None
                else credential.normalized_email
            )
            if target == credential.normalized_email and user.email_verified:
                return None
            # Do not change the profile or login identifier until ownership is proven.
            return await self._issue(session, user_id, target, 'email_verification')

    async def _consume(self, session, raw_token: SecretStr, purpose: str):
        digest = token_digest(raw_token)
        record = await session.get(AuthActionToken, digest)
        now = datetime.now(UTC)
        if (
            record is None
            or record.purpose != purpose
            or record.expires_at <= now
            or record.consumed_at
        ):
            raise InvalidActionToken('The link is invalid or has expired.')
        try:
            user, credential = await lock_account(session, record.user_id)
        except InvalidCredentials:
            raise InvalidActionToken('The link is invalid or has expired.') from None
        now = datetime.now(UTC)
        # Conditional UPDATE is essential even with row locks: SQLite unit tests
        # and requests which read a token before another request consumed it.
        consumed = await session.scalar(
            update(AuthActionToken)
            .where(
                AuthActionToken.token_digest == digest,
                AuthActionToken.purpose == purpose,
                AuthActionToken.consumed_at.is_(None),
                AuthActionToken.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(AuthActionToken.token_digest)
        )
        if consumed is None:
            raise InvalidActionToken('The link is invalid or has expired.')
        return record, user, credential

    async def reset_password(self, token: SecretStr, password: SecretStr) -> UUID:
        hashed = await hash_password(password)
        async with self.session_factory() as session, session.begin():
            record, user, credential = await self._consume(
                session, token, 'password_reset'
            )
            if record.email != credential.normalized_email:
                raise InvalidActionToken('The link is invalid or has expired.')
            credential.password_hash = hashed
            credential.must_change_password = False
            await revoke_all_in_transaction(session, user.id)
            await session.execute(
                delete(AuthActionToken).where(
                    AuthActionToken.user_id == user.id,
                    AuthActionToken.token_digest != record.token_digest,
                )
            )
            return user.id

    async def verify_email(self, token: SecretStr) -> UUID:
        try:
            async with self.session_factory() as session, session.begin():
                record, user, credential = await self._consume(
                    session, token, 'email_verification'
                )
                if record.email is None:
                    raise InvalidActionToken('The link is invalid or has expired.')
                replacement = record.email != credential.normalized_email
                from server.auth.user_management import EnterpriseUserManagementService

                await EnterpriseUserManagementService.apply_verified_email(
                    session, user, credential, record.email
                )
                if replacement:
                    await revoke_all_in_transaction(session, user.id)
                await session.execute(
                    delete(AuthActionToken).where(
                        AuthActionToken.user_id == user.id,
                        AuthActionToken.token_digest != record.token_digest,
                    )
                )
                await session.flush()
                return user.id
        except IntegrityError:
            raise InvalidActionToken(
                'The link cannot be used for this email address.'
            ) from None

    async def enroll(
        self, invitation_token: SecretStr, password: SecretStr
    ) -> IssuedSession:
        try:
            async with self.session_factory() as session, session.begin():
                invitation = await session.scalar(
                    select(OrgInvitation)
                    .where(OrgInvitation.token == invitation_token.get_secret_value())
                    .with_for_update()
                )
                now = datetime.now(UTC).replace(tzinfo=None)
                if (
                    invitation is None
                    or invitation.status != OrgInvitation.STATUS_PENDING
                    or invitation.expires_at <= now
                ):
                    raise InvalidActionToken(
                        'The invitation is invalid or has expired.'
                    )
                # Email comes exclusively from the proven invitation, never input.
                user = await create_local_account(
                    session,
                    invitation.email,
                    password,
                    must_change_password=False,
                    email_verified=True,
                )
                now = datetime.now(UTC).replace(tzinfo=None)
                claimed = await session.scalar(
                    update(OrgInvitation)
                    .where(
                        OrgInvitation.id == invitation.id,
                        OrgInvitation.status == OrgInvitation.STATUS_PENDING,
                        OrgInvitation.expires_at > now,
                    )
                    .values(
                        status=OrgInvitation.STATUS_ACCEPTED,
                        accepted_at=now,
                        accepted_by_user_id=user.id,
                    )
                    .returning(OrgInvitation.id)
                )
                if claimed is None:
                    raise InvalidActionToken(
                        'The invitation is invalid or has expired.'
                    )
                session.add(
                    OrgMember(
                        org_id=invitation.org_id,
                        user_id=user.id,
                        role_id=invitation.role_id,
                        status='active',
                        llm_api_key=SecretStr(''),
                        agent_settings_diff={},
                        conversation_settings_diff={},
                    )
                )
                user.current_org_id = invitation.org_id
                credential = await session.get(LocalCredentials, user.id)
                assert credential is not None
                return await issue_in_transaction(session, user, credential)
        except (AccountConflict, IntegrityError):
            raise AccountConflict(
                'An account already uses this invitation email. Sign in to accept it.'
            ) from None
