"""Native user-owned provider credentials, independent of application login."""

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from hmac import compare_digest
from urllib.parse import urlencode
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.integrations.provider import ProviderToken
from openhands.app_server.integrations.service_types import ProviderType
from server.auth.native_git_config import git_config, native_git_capabilities
from server.auth.native_session import digest_token, new_token
from server.auth.native_types import (
    GitConnectionList,
    GitConnectionView,
    SessionFactory,
)
from server.services.native_auth_service import NativePrincipal
from server.services.native_git_provider import (
    GitCredentialError,
    GitGrant,
    GitIdentity,
    exchange_grant,
    revoke_grant,
    verify_provider_identity,
)
from storage.database import a_session_maker
from storage.encrypt_utils import get_jwt_service
from storage.native_auth import AuthAccount, BrowserSession
from storage.native_git import GitConnection, GitOAuthState
from storage.user import User


def now() -> datetime:
    return datetime.now(UTC)


def connection_view(row: GitConnection) -> GitConnectionView:
    return {
        'provider': row.provider,
        'host': row.host,
        'auth_type': row.auth_type,
        'status': 'reconnect_required'
        if row.last_error in ('credential_rejected', 'insufficient_scope')
        else 'connected',
        'account': {
            'id': row.subject,
            'login': row.login,
            'display_name': row.display_name,
            'avatar_url': row.avatar_url,
        },
        'last_error': row.last_error,
    }


class NativeGitCredentialService:
    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self.sessions = session_factory or a_session_maker

    async def _active(self, session: AsyncSession, account_id: UUID) -> None:
        # Lifecycle writers update AuthAccount/User before credentials. Keep this
        # lock order, and hold account proof through provider resolution/rotation.
        active = await session.scalar(
            select(AuthAccount)
            .select_from(AuthAccount)
            .join(User, User.id == AuthAccount.id)
            .where(
                AuthAccount.id == account_id,
                AuthAccount.state == 'profile_present',
                User.is_disabled.is_(False),
            )
            .with_for_update(read=True, of=(AuthAccount, User))
        )
        from server.services.native_account_service import account_admitted

        if active is None or not await account_admitted(session, active):
            raise GitCredentialError('account_unavailable', 403)

    async def _browser(self, session: AsyncSession, principal: NativePrincipal) -> None:
        await self._active(session, principal.account_id)
        browser = await session.scalar(
            select(BrowserSession)
            .join(AuthAccount, AuthAccount.id == BrowserSession.account_id)
            .where(
                BrowserSession.id == principal.session_id,
                BrowserSession.account_id == principal.account_id,
                BrowserSession.revoked_at.is_(None),
                BrowserSession.idle_expires_at > now(),
                BrowserSession.absolute_expires_at > now(),
                BrowserSession.session_version == AuthAccount.session_version,
            )
            .with_for_update(read=True, of=BrowserSession)
        )
        if browser is None:
            raise GitCredentialError('browser_session_required', 403)
        from server.services.native_account_service import session_method_admitted

        account = await session.get(AuthAccount, principal.account_id)
        if account is None or not await session_method_admitted(
            session, account, browser
        ):
            raise GitCredentialError('browser_session_required', 403)

    async def _connection(
        self, session: AsyncSession, account_id: UUID, provider: str, host: str
    ) -> GitConnection:
        # A tombstone exists before requests leave this process. OAuth states bind
        # its generation so disconnect also cancels a first-time pending connect.
        await session.execute(
            insert(GitConnection)
            .values(
                id=uuid4(),
                account_id=account_id,
                provider=provider,
                host=host,
                auth_type='pat',
                generation=0,
                revoked_at=now(),
                created_at=now(),
                updated_at=now(),
            )
            .on_conflict_do_nothing(constraint='uq_native_git_account_provider')
        )
        row = await session.scalar(
            select(GitConnection)
            .where(
                GitConnection.account_id == account_id,
                GitConnection.provider == provider,
            )
            .with_for_update()
        )
        assert row is not None
        return row

    async def list_connections(self, user_id: str | UUID) -> GitConnectionList:
        account_id = UUID(str(user_id))
        async with self.sessions() as session, session.begin():
            await self._active(session, account_id)
            rows = (
                await session.scalars(
                    select(GitConnection)
                    .where(
                        GitConnection.account_id == account_id,
                        GitConnection.revoked_at.is_(None),
                    )
                    .order_by(GitConnection.provider)
                )
            ).all()
            connections = [connection_view(row) for row in rows]
        return {'connections': connections, 'capabilities': native_git_capabilities()}

    async def connect_manual(
        self,
        user_id: str | UUID,
        provider: str,
        token: str,
        host: str | None = None,
        email: str | None = None,
    ) -> GitConnectionView:
        config = git_config(provider, host)
        method = 'api_token' if provider == 'bitbucket' else 'pat'
        if method not in config.methods:
            raise GitCredentialError('manual_connection_disabled', 403)
        if (
            not token
            or len(token) > 16384
            or any(ord(c) < 33 or ord(c) > 126 for c in token)
        ):
            raise GitCredentialError('invalid_credential')
        if provider == 'bitbucket':
            if (
                not email
                or ':' in email
                or '@' not in email
                or len(email) > 320
                or any(ord(c) < 33 for c in email)
            ):
                raise GitCredentialError('bitbucket_email_required')
        elif email:
            raise GitCredentialError('unexpected_email')
        account_id = UUID(str(user_id))
        async with self.sessions() as session, session.begin():
            await self._active(session, account_id)
            row = await self._connection(session, account_id, provider, config.host)
            generation = row.generation
        identity = await verify_provider_identity(config, token, email, manual=True)
        return await self._save(
            account_id,
            provider,
            config.host,
            method,
            GitGrant(token),
            identity,
            generation,
            email=email,
        )

    async def _save(
        self,
        account_id: UUID,
        provider: str,
        host: str,
        auth_type: str,
        grant: GitGrant,
        identity: GitIdentity,
        generation: int,
        email: str | None = None,
        principal: NativePrincipal | None = None,
    ) -> GitConnectionView:
        crypto = get_jwt_service()
        try:
            async with self.sessions() as session, session.begin():
                if principal:
                    await self._browser(session, principal)
                else:
                    await self._active(session, account_id)
                row = await self._connection(session, account_id, provider, host)
                if row.generation != generation:
                    raise GitCredentialError('connection_changed', 409)
                if row.revoked_at is None and (
                    row.host != host or row.subject != identity.id
                ):
                    raise GitCredentialError('provider_account_conflict', 409)
                row.host = host
                row.subject = identity.id
                row.identity_verified = True
                row.login = identity.login
                row.display_name = identity.display_name
                row.avatar_url = identity.avatar_url
                row.auth_type = auth_type
                row.encrypted_access_token = crypto.encrypt_value(grant.access_token)
                row.encrypted_refresh_token = (
                    crypto.encrypt_value(grant.refresh_token)
                    if grant.refresh_token
                    else None
                )
                row.encrypted_email = crypto.encrypt_value(email) if email else None
                row.expires_at = grant.expires_at
                row.refresh_expires_at = grant.refresh_expires_at
                row.last_error = None
                row.revoked_at = None
                row.generation += 1
                await session.flush()
                result = connection_view(row)
            if provider == 'gitlab':
                from server.auth.gitlab_sync import schedule_gitlab_repo_sync

                schedule_gitlab_repo_sync(str(account_id))
            return result
        except IntegrityError as exc:
            raise GitCredentialError('provider_account_conflict', 409) from exc

    async def start_oauth(
        self, principal: NativePrincipal, provider: str, host: str | None = None
    ) -> str:
        config = git_config(provider, host)
        if 'oauth' not in config.methods:
            raise GitCredentialError('oauth_connection_disabled', 403)
        state = new_token()
        verifier = new_token() if provider in ('github', 'gitlab') else None
        async with self.sessions() as session, session.begin():
            await self._browser(session, principal)
            row = await self._connection(
                session, principal.account_id, provider, config.host
            )
            if row.revoked_at is None and row.host != config.host:
                raise GitCredentialError('provider_account_conflict', 409)
            await session.execute(
                update(GitOAuthState)
                .where(
                    GitOAuthState.account_id == principal.account_id,
                    GitOAuthState.provider == provider,
                    GitOAuthState.consumed_at.is_(None),
                )
                .values(consumed_at=now())
            )
            session.add(
                GitOAuthState(
                    token_digest=digest_token(state, 'git-oauth'),
                    account_id=principal.account_id,
                    session_id=principal.session_id,
                    provider=provider,
                    host=config.host,
                    connection_generation=row.generation,
                    encrypted_verifier=get_jwt_service().encrypt_value(verifier)
                    if verifier
                    else None,
                    expires_at=now() + timedelta(minutes=10),
                )
            )
        params = {
            'client_id': config.client_id,
            'redirect_uri': config.callback_url,
            'response_type': 'code',
            'state': state,
        }
        if verifier:
            params.update(
                code_challenge=base64.urlsafe_b64encode(
                    hashlib.sha256(verifier.encode()).digest()
                )
                .rstrip(b'=')
                .decode(),
                code_challenge_method='S256',
            )
        if provider == 'gitlab':
            params['scope'] = 'api'
        elif provider == 'github':
            params['scope'] = 'repo read:user'
        # Bitbucket confidential consumers use client-secret authentication;
        # its documented OAuth endpoint has no PKCE support.
        return f'{config.authorize_url}?{urlencode(params)}'

    async def complete_oauth(
        self,
        principal: NativePrincipal,
        provider: str,
        state: str,
        code: str | None,
        error: str | None = None,
    ) -> str:
        if not 32 <= len(state) <= 128:
            raise GitCredentialError('oauth_state_invalid')
        async with self.sessions() as session, session.begin():
            await self._browser(session, principal)
            row = await session.scalar(
                select(GitOAuthState)
                .where(GitOAuthState.token_digest == digest_token(state, 'git-oauth'))
                .with_for_update()
            )
            if (
                row is None
                or row.account_id != principal.account_id
                or row.session_id != principal.session_id
                or row.provider != provider
                or row.expires_at <= now()
                or row.consumed_at is not None
            ):
                raise GitCredentialError('oauth_state_invalid')
            row.consumed_at = now()
            host, generation = row.host, row.connection_generation
            verifier = (
                get_jwt_service().decrypt_value(row.encrypted_verifier)
                if row.encrypted_verifier
                else None
            )
            row.encrypted_verifier = None
        # Consume before external exchange. Cancellation/errors are single-use too.
        if error:
            if error == 'access_denied':
                return 'cancelled'
            raise GitCredentialError('oauth_authorization_failed')
        if not code or len(code) > 4096:
            raise GitCredentialError('oauth_code_invalid')
        config = git_config(provider, host)
        if 'oauth' not in config.methods:
            raise GitCredentialError('oauth_connection_disabled', 403)
        grant = await exchange_grant(config, code=code, verifier=verifier)
        identity = await verify_provider_identity(config, grant.access_token)
        await self._save(
            principal.account_id,
            provider,
            host,
            'oauth',
            grant,
            identity,
            generation,
            principal=principal,
        )
        return 'connected'

    async def disconnect(self, user_id: str | UUID, provider: str) -> None:
        from server.auth.native_git_config import CORE_HOSTS

        if provider not in CORE_HOSTS:
            raise GitCredentialError('invalid_provider_configuration', 400)
        revoke_token = None
        config = None
        async with self.sessions() as session, session.begin():
            await self._active(session, UUID(str(user_id)))
            row = await self._connection(
                session, UUID(str(user_id)), provider, CORE_HOSTS[provider]
            )
            try:
                config = git_config(provider, row.host)
            except ValueError:
                pass  # Local disconnect still works after an operator disables a provider.
            if (
                row.revoked_at is None
                and row.auth_type == 'oauth'
                and row.encrypted_access_token
            ):
                revoke_token = get_jwt_service().decrypt_value(
                    row.encrypted_access_token
                )
            self._erase(row)
            await session.execute(
                update(GitOAuthState)
                .where(
                    GitOAuthState.account_id == UUID(str(user_id)),
                    GitOAuthState.provider == provider,
                )
                .values(consumed_at=now(), encrypted_verifier=None)
            )
        if revoke_token and config:
            await revoke_grant(config, revoke_token)

    @staticmethod
    def _erase(row: GitConnection) -> None:
        row.revoked_at = now()
        row.encrypted_access_token = None
        row.encrypted_refresh_token = None
        row.encrypted_email = None
        row.last_error = None
        row.generation += 1

    async def disconnect_all(self, account_id: UUID, session: AsyncSession) -> None:
        rows = (
            await session.scalars(
                select(GitConnection)
                .where(GitConnection.account_id == account_id)
                .order_by(GitConnection.provider)
                .with_for_update()
            )
        ).all()
        for row in rows:
            self._erase(row)
            row.subject = row.login = row.display_name = row.avatar_url = None
        await session.execute(
            update(GitOAuthState)
            .where(GitOAuthState.account_id == account_id)
            .values(consumed_at=now(), encrypted_verifier=None)
        )

    async def mark_rejected_by_authorization(
        self, user_id: str, provider: str, authorization: str
    ) -> None:
        """Only mark the credential that actually received the provider's 401.

        An old in-flight request cannot invalidate a newly connected credential.
        Keep ciphertext for explicit reconnect instead of deleting on errors.
        """
        async with self.sessions() as session, session.begin():
            row = await session.scalar(
                select(GitConnection)
                .where(
                    GitConnection.account_id == UUID(user_id),
                    GitConnection.provider == provider,
                    GitConnection.revoked_at.is_(None),
                )
                .with_for_update()
            )
            if row is None or not row.encrypted_access_token:
                return
            crypto = get_jwt_service()
            value = crypto.decrypt_value(row.encrypted_access_token)
            expected = f'Bearer {value}'
            if row.auth_type == 'api_token' and row.encrypted_email:
                value = f'{crypto.decrypt_value(row.encrypted_email)}:{value}'
                expected = 'Basic ' + base64.b64encode(value.encode()).decode()
            if compare_digest(expected, authorization):
                row.last_error = 'credential_rejected'

    async def revoke_provider_subject(
        self, provider: str, host: str, subject: str
    ) -> None:
        """Apply a provider-signed user authorization revocation, without email lookup."""
        config = git_config(provider, host)
        async with self.sessions() as session, session.begin():
            row = await session.scalar(
                select(GitConnection)
                .where(
                    GitConnection.provider == provider,
                    GitConnection.host == config.host,
                    GitConnection.subject == subject,
                    GitConnection.auth_type == 'oauth',
                    GitConnection.revoked_at.is_(None),
                )
                .with_for_update()
            )
            if row is not None:
                self._erase(row)
                await session.execute(
                    update(GitOAuthState)
                    .where(
                        GitOAuthState.account_id == row.account_id,
                        GitOAuthState.provider == provider,
                    )
                    .values(consumed_at=now(), encrypted_verifier=None)
                )

    async def get_token(
        self, user_id: str | UUID, provider: ProviderType
    ) -> ProviderToken:
        account_id = UUID(str(user_id))
        failure = None
        result = None
        crypto = get_jwt_service()
        async with self.sessions() as session, session.begin():
            await self._active(session, account_id)
            row = await session.scalar(
                select(GitConnection)
                .where(
                    GitConnection.account_id == account_id,
                    GitConnection.provider == provider.value,
                )
                .with_for_update()
            )
            if (
                row is None
                or row.revoked_at is not None
                or not row.encrypted_access_token
            ):
                raise GitCredentialError('provider_not_connected', 409)
            try:
                config = git_config(row.provider, row.host)
            except ValueError as exc:
                raise GitCredentialError('provider_disabled', 409) from exc
            if row.last_error in ('credential_rejected', 'insufficient_scope'):
                raise GitCredentialError(row.last_error)
            if row.expires_at is not None and row.expires_at <= now() + timedelta(
                seconds=60
            ):
                if (
                    not row.encrypted_refresh_token
                    or row.auth_type != 'oauth'
                    or (
                        row.refresh_expires_at is not None
                        and row.refresh_expires_at <= now()
                    )
                ):
                    failure = GitCredentialError('credential_rejected')
                else:
                    try:
                        grant = await exchange_grant(
                            config,
                            refresh_token=crypto.decrypt_value(
                                row.encrypted_refresh_token
                            ),
                        )
                        row.encrypted_access_token = crypto.encrypt_value(
                            grant.access_token
                        )
                        row.encrypted_refresh_token = (
                            crypto.encrypt_value(grant.refresh_token)
                            if grant.refresh_token
                            else row.encrypted_refresh_token
                        )
                        row.expires_at = grant.expires_at
                        row.refresh_expires_at = (
                            grant.refresh_expires_at or row.refresh_expires_at
                        )
                        row.last_error = None
                        row.identity_verified = False
                        identity = await verify_provider_identity(
                            config, grant.access_token
                        )
                        if identity.id != row.subject:
                            raise GitCredentialError('credential_rejected')
                        row.identity_verified = True
                    except GitCredentialError as exc:
                        failure = exc
                if failure:
                    row.last_error = failure.code
            if not failure and not row.identity_verified:
                try:
                    identity = await verify_provider_identity(
                        config, crypto.decrypt_value(row.encrypted_access_token)
                    )
                    if identity.id != row.subject:
                        raise GitCredentialError('credential_rejected')
                    row.identity_verified = True
                    row.last_error = None
                except GitCredentialError as exc:
                    failure = exc
                    row.last_error = exc.code
            if not failure:
                value = crypto.decrypt_value(row.encrypted_access_token)
                # The provider abstraction already supports Basic username:token
                # for Bitbucket; OAuth access tokens remain plain Bearer values.
                if row.auth_type == 'api_token' and row.encrypted_email:
                    value = f'{crypto.decrypt_value(row.encrypted_email)}:{value}'
                result = ProviderToken(
                    token=SecretStr(value), user_id=row.subject, host=row.host
                )
        # Rotation transaction commits before any token escapes this service.
        if failure:
            raise failure
        assert result is not None
        return result

    async def get_provider_tokens(
        self, user_id: str | UUID
    ) -> dict[ProviderType, ProviderToken]:
        connections = (await self.list_connections(user_id))['connections']
        result = {}
        for connection in connections:
            provider = ProviderType(connection['provider'])
            try:
                result[provider] = await self.get_token(user_id, provider)
            except GitCredentialError as exc:
                if exc.code == 'account_unavailable':
                    raise
                # Optional provider failure cannot prevent ordinary app settings,
                # another provider, API keys, or sandbox launch without git.
        return result

    async def resolve_actor(
        self, provider: ProviderType, host: str, subject: str
    ) -> str | None:
        config = git_config(provider.value, host)
        async with self.sessions() as session:
            row = await session.scalar(
                select(GitConnection).where(
                    GitConnection.provider == provider.value,
                    GitConnection.host == config.host,
                    GitConnection.subject == subject,
                    GitConnection.revoked_at.is_(None),
                )
            )
            if row is None:
                return None
            account_id, generation = row.account_id, row.generation
        try:
            token = await self.get_token(account_id, provider)
            if token.host != config.host or token.user_id != subject:
                return None
            assert token.token is not None
            value = token.token.get_secret_value()
            email = None
            if provider == ProviderType.BITBUCKET and ':' in value:
                email, value = value.split(':', 1)
            identity = await verify_provider_identity(config, value, email)
            if identity.id != subject:
                return None
            async with self.sessions() as session, session.begin():
                await self._active(session, account_id)
                current = await session.scalar(
                    select(GitConnection)
                    .where(
                        GitConnection.account_id == account_id,
                        GitConnection.provider == provider.value,
                    )
                    .with_for_update(read=True)
                )
                if (
                    current is None
                    or current.revoked_at is not None
                    or not current.identity_verified
                    or current.host != config.host
                    or current.subject != subject
                    or current.generation != generation
                ):
                    return None
                return str(account_id)
        except GitCredentialError:
            return None


@lru_cache(maxsize=1)
def get_native_git_service() -> NativeGitCredentialService:
    return NativeGitCredentialService()
