"""Local identity and opaque browser-session security primitives."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from hmac import compare_digest
from urllib.parse import quote, unquote
from uuid import UUID, uuid4

from sqlalchemy import case, delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.auth_config import get_native_auth_settings
from server.auth.native_password import (
    NativeAuthError,
)
from server.auth.native_session import csrf_for_token, digest_token, new_token
from server.auth.native_types import (
    NativeProfileMetadata,
    SessionFactory,
)
from server.constants import DEPLOYMENT_MODE
from storage.database import a_session_maker
from storage.native_auth import (
    AuthAccount,
    AuthChallenge,
    AuthThrottle,
    BrowserSession,
    PasswordCredential,
)
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User

SessionRow = tuple[BrowserSession, AuthAccount, PasswordCredential | None, User]


@dataclass(frozen=True)
class NativePrincipal:
    account_id: UUID
    email: str
    session_id: UUID | None = None
    auth_time: datetime | None = None
    auth_method: str | None = None


@dataclass(frozen=True)
class NativeLogin:
    principal: NativePrincipal
    token: str = field(repr=False)
    redirect_to: str = '/'


def _now() -> datetime:
    return datetime.now(UTC)


def _valid_token(token: str | None) -> bool:
    return bool(token and 32 <= len(token) <= 128 and token.isascii())


def safe_return_path(path: str | None) -> str:
    if not path or len(path) > 2048:
        return '/'
    decoded = unquote(path)
    if (
        not decoded.startswith('/')
        or decoded.startswith('//')
        or '\\' in decoded
        or any(ord(c) < 32 for c in decoded)
    ):
        return '/'
    return path


async def _redirect(
    session: AsyncSession, user: User, return_path: str | None = None
) -> str:
    path = safe_return_path(return_path)
    if user.accepted_tos is None:
        return f'/accept-tos?redirect_url={quote(path, safe="")}'
    if user.onboarding_completed is False:
        first_owner = await session.scalar(
            select(User.id)
            .join(OrgMember, OrgMember.user_id == User.id)
            .join(Role, Role.id == OrgMember.role_id)
            .where(
                OrgMember.org_id == user.current_org_id,
                Role.name == 'owner',
                User.accepted_tos.is_not(None),
            )
            .order_by(User.accepted_tos)
            .limit(1)
        )
        if DEPLOYMENT_MODE == 'cloud' or (
            DEPLOYMENT_MODE == 'self_hosted' and first_owner == user.id
        ):
            return f'/onboarding?returnTo={quote(path, safe="")}'
    return path


class NativeAuthService:
    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self.sessions: SessionFactory = session_factory or a_session_maker

    async def throttle(
        self, namespace: str, client_ip: str, email: str | None = None
    ) -> None:
        """Commit limits before credential verification, including failed attempts."""
        now = _now()
        cutoff = now - timedelta(seconds=300)
        keys = [(f'{namespace}:global', 1000), (f'{namespace}:ip:{client_ip}', 50)]
        if email is not None:
            keys.append((f'{namespace}:account:{email}', 10))
        blocked = False
        try:
            async with self.sessions() as session, session.begin():
                # Stale counters do not accumulate, even without a running cron job.
                await session.execute(
                    delete(AuthThrottle).where(AuthThrottle.window_start < cutoff)
                )
                for key, limit in keys:
                    stmt = insert(AuthThrottle).values(
                        key_digest=digest_token(key, 'throttle'),
                        window_start=now,
                        attempts=1,
                    )
                    counter_stmt = stmt.on_conflict_do_update(
                        index_elements=[AuthThrottle.key_digest],
                        set_={
                            'attempts': case(
                                (AuthThrottle.window_start < cutoff, 1),
                                else_=AuthThrottle.attempts + 1,
                            ),
                            'window_start': case(
                                (AuthThrottle.window_start < cutoff, now),
                                else_=AuthThrottle.window_start,
                            ),
                        },
                    ).returning(AuthThrottle.attempts)
                    attempts = await session.scalar(counter_stmt)
                    blocked = blocked or bool(attempts and attempts > limit)
        except SQLAlchemyError as exc:
            raise NativeAuthError(
                'Authentication temporarily unavailable', 503
            ) from exc
        if blocked:
            raise NativeAuthError(
                'Too many authentication attempts; try again later', 429
            )

    async def get_identity(self, account_id: UUID) -> NativePrincipal | None:
        from server.services.native_account_service import account_admitted

        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(AuthAccount, User)
                    .join(User, User.id == AuthAccount.id)
                    .where(AuthAccount.id == account_id)
                )
            ).first()
            if row is None or row[0].state != 'profile_present' or row[1].is_disabled:
                return None
            if row[0].display_email is None or not await account_admitted(
                session, row[0]
            ):
                return None
            return NativePrincipal(account_id=account_id, email=row[0].display_email)

    async def _session(
        self, session: AsyncSession, token: str, *, renew: bool = False
    ) -> SessionRow | None:
        from server.services.native_account_service import session_method_admitted

        if not _valid_token(token):
            return None
        row = (
            await session.execute(
                select(BrowserSession, AuthAccount, PasswordCredential, User)
                .join(AuthAccount, AuthAccount.id == BrowserSession.account_id)
                .outerjoin(
                    PasswordCredential, PasswordCredential.account_id == AuthAccount.id
                )
                .join(User, User.id == AuthAccount.id)
                .where(BrowserSession.token_digest == digest_token(token, 'session'))
                .with_for_update(of=BrowserSession)
            )
        ).first()
        if row is None:
            return None
        browser, account, credential, user = row
        now = _now()
        if (
            browser.revoked_at is not None
            or browser.idle_expires_at <= now
            or browser.absolute_expires_at <= now
            or account.state != 'profile_present'
            or user.is_disabled
            or browser.session_version != account.session_version
            or account.normalized_email is None
            or account.display_email is None
            or not await session_method_admitted(session, account, browser)
        ):
            return None
        if renew:
            browser.idle_expires_at = min(
                now + timedelta(seconds=get_native_auth_settings().idle_seconds),
                browser.absolute_expires_at,
            )
        return browser, account, credential, user

    async def authenticate_session(self, token: str) -> NativePrincipal | None:
        async with self.sessions() as session, session.begin():
            row = await self._session(session, token, renew=True)
            if row is None:
                return None
            browser, account, _, _ = row
            if account.display_email is None:
                return None
            return NativePrincipal(
                account.id,
                account.display_email,
                browser.id,
                browser.auth_time,
                browser.auth_method,
            )

    async def revoke_session(self, token: str) -> None:
        if not _valid_token(token):
            return
        async with self.sessions() as session, session.begin():
            browser = await session.scalar(
                select(BrowserSession)
                .where(BrowserSession.token_digest == digest_token(token, 'session'))
                .with_for_update()
            )
            if browser is not None:
                browser.revoked_at = _now()

    async def _new_session(
        self,
        session: AsyncSession,
        account: AuthAccount,
        credential: PasswordCredential | None,
        user: User,
        return_path: str | None = None,
        *,
        auth_method: str = 'password',
        auth_time: datetime | None = None,
        session_expiry_bound: datetime | None = None,
    ) -> NativeLogin:
        if account.display_email is None:
            raise NativeAuthError('Account metadata is unavailable', 409)
        now, token = _now(), new_token()
        config = get_native_auth_settings()
        authenticated_at = auth_time or now
        if authenticated_at.tzinfo is None or authenticated_at > now + timedelta(
            seconds=60
        ):
            raise NativeAuthError('Invalid authentication time', 400)
        absolute_expiry = now + timedelta(seconds=config.absolute_seconds)
        if session_expiry_bound is not None:
            if session_expiry_bound.tzinfo is None or session_expiry_bound <= now:
                raise NativeAuthError('Authentication has expired', 401)
            absolute_expiry = min(absolute_expiry, session_expiry_bound)
        browser = BrowserSession(
            id=uuid4(),
            token_digest=digest_token(token, 'session'),
            account_id=account.id,
            session_version=account.session_version,
            credential_version=credential.credential_version if credential else 0,
            auth_method=auth_method,
            auth_time=authenticated_at,
            idle_expires_at=min(
                now + timedelta(seconds=config.idle_seconds), absolute_expiry
            ),
            absolute_expires_at=absolute_expiry,
        )
        session.add(browser)
        user.first_login_at = user.first_login_at or now.replace(tzinfo=None)
        user.last_login_at = now.replace(tzinfo=None)
        await session.flush()
        return NativeLogin(
            NativePrincipal(
                account.id,
                account.display_email,
                browser.id,
                authenticated_at,
                auth_method,
            ),
            token,
            await _redirect(session, user, return_path),
        )

    async def issue_csrf(self, session_token: str | None) -> tuple[str, str | None]:
        if session_token and await self.authenticate_session(session_token) is not None:
            return csrf_for_token(session_token), None
        token = new_token()
        async with self.sessions() as session, session.begin():
            await session.execute(
                delete(AuthChallenge).where(
                    AuthChallenge.expires_at < _now(),
                    AuthChallenge.purpose == 'anonymous_csrf',
                )
            )
            session.add(
                AuthChallenge(
                    token_digest=digest_token(token, 'anonymous_csrf'),
                    purpose='anonymous_csrf',
                    expires_at=_now() + timedelta(minutes=10),
                )
            )
        return csrf_for_token(token), token

    async def validate_csrf(
        self,
        session_token: str | None,
        anonymous_token: str | None,
        csrf_token: str | None,
    ) -> bool:
        if not csrf_token or len(csrf_token) != 64:
            return False
        if session_token:
            return await self.authenticate_session(
                session_token
            ) is not None and compare_digest(csrf_for_token(session_token), csrf_token)
        if not _valid_token(anonymous_token):
            return False
        assert anonymous_token is not None
        if not compare_digest(csrf_for_token(anonymous_token), csrf_token):
            return False
        async with self.sessions() as session:
            challenge = await session.scalar(
                select(AuthChallenge).where(
                    AuthChallenge.token_digest
                    == digest_token(anonymous_token, 'anonymous_csrf'),
                    AuthChallenge.purpose == 'anonymous_csrf',
                )
            )
            return bool(
                challenge
                and challenge.expires_at > _now()
                and challenge.revoked_at is None
                and challenge.consumed_at is None
            )

    async def _recent(
        self, session: AsyncSession, token: str, account_id: UUID
    ) -> SessionRow:
        row = await self._session(session, token)
        if (
            row is None
            or row[1].id != account_id
            or row[0].auth_time
            <= _now()
            - timedelta(seconds=get_native_auth_settings().recent_auth_seconds)
        ):
            raise NativeAuthError('Sign in again to perform this action', 401)
        return row

    @staticmethod
    async def authentication_methods(
        session: AsyncSession, account_id: UUID
    ) -> list[str]:
        return (
            ['password']
            if await session.get(PasswordCredential, account_id) is not None
            else []
        )

    async def profile_metadata(self, account_id: UUID) -> NativeProfileMetadata:
        """Public local identity metadata, with no credentials or IdP subjects."""
        async with self.sessions() as session:
            account = await session.get(AuthAccount, account_id)
            if account is None or account.state != 'profile_present':
                raise NativeAuthError('Account is unavailable', 401)
            methods = await self.authentication_methods(session, account_id)
            return {
                'email': account.display_email,
                'has_password': 'password' in methods,
                'authentication_methods': methods,
            }

    async def cleanup_expired_state(self) -> None:
        """Keep expired secrets briefly for incident review; no raw tokens exist."""
        cutoff = _now() - timedelta(days=7)
        async with self.sessions() as session, session.begin():
            await session.execute(
                delete(BrowserSession).where(
                    or_(
                        BrowserSession.absolute_expires_at < cutoff,
                        BrowserSession.revoked_at < cutoff,
                    )
                )
            )
            await session.execute(
                delete(AuthChallenge).where(AuthChallenge.expires_at < cutoff)
            )
            await session.execute(
                delete(AuthThrottle).where(
                    AuthThrottle.window_start < _now() - timedelta(minutes=5)
                )
            )


@lru_cache(maxsize=1)
def get_native_auth_service() -> NativeAuthService:
    return NativeAuthService()
