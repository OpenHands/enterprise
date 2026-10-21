"""Store for ``oauth_tokens`` — per-user credential pairs with unified refresh.

``get_valid_access_token`` is the heart of the unified refresh workflow:

1. **Fast path**: read the row without a lock. If the access token is still
   valid (expiry is ``NULL`` == never expires, or ``> now + drift``), return it.
2. **Slow path**: the access token is expired within the drift margin. Acquire
   ``SELECT ... FOR UPDATE`` after ``SET LOCAL lock_timeout = '5s'``.
3. **Double-check**: re-read; another worker may have refreshed while we waited.
4. If still expired and the refresh token is also expired (``NULL`` == never),
   call the ``refresh`` callback to mint a new pair and persist it.

``NULL`` expiry means "never expires" — there is no ``0`` sentinel. An expired
access token remains refreshable as long as the refresh token is valid.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.auth_error import TokenRefreshError
from storage.database import a_session_maker
from storage.oauth_token import OAuthToken

# Database lock timeout to prevent indefinite blocking during refresh.
LOCK_TIMEOUT_SECONDS = 5


# ── token wrapping helpers ────────────────────────────────────────────────
# ``access_token`` / ``refresh_token`` columns are ``EncryptedJSON`` (a dict
# must round-trip), but callers want plain strings. Wrap/unwrap with a
# single-key dict so the encryption boundary stays on the column.


def wrap_token(plaintext: str) -> dict[str, str]:
    return {'v': plaintext}


def unwrap_token(blob: dict[str, str] | None) -> str | None:
    if blob is None:
        return None
    return blob.get('v')


# A refresh callback receives the decrypted refresh token and the current
# expiry datetimes, and returns the new (encrypted-ready) token pair plus new
# expiry datetimes. ``None`` means "no refresh performed".
RefreshResult = dict[str, object]
RefreshCallback = Callable[
    [str, datetime | None, datetime | None],
    Awaitable[RefreshResult | None],
]


def _is_expired(expires_at: datetime | None, drift: timedelta) -> bool:
    """``None`` means never expires; otherwise compare against now + drift."""
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc) + drift


@dataclass
class OAuthTokenStore:
    """CRUD + unified refresh for a user's token at one provider."""

    user_id: UUID
    oauth_provider_id: int

    # ── write ──────────────────────────────────────────────────────────────

    async def store_tokens(
        self,
        access_token: str,
        refresh_token: str | None,
        access_token_expires_at: datetime | None,
        refresh_token_expires_at: datetime | None,
    ) -> OAuthToken:
        """Insert or update the token pair for this user + provider."""
        async with a_session_maker() as session:
            result = await session.execute(
                select(OAuthToken).where(
                    OAuthToken.user_id == self.user_id,
                    OAuthToken.oauth_provider_id == self.oauth_provider_id,
                )
            )
            row = result.scalars().one_or_none()
            if row is not None:
                row.access_token = wrap_token(access_token)
                row.refresh_token = wrap_token(refresh_token) if refresh_token else None
                row.access_token_expires_at = access_token_expires_at
                row.refresh_token_expires_at = refresh_token_expires_at
            else:
                row = OAuthToken(
                    user_id=self.user_id,
                    oauth_provider_id=self.oauth_provider_id,
                    access_token=wrap_token(access_token),
                    refresh_token=(
                        wrap_token(refresh_token) if refresh_token else None
                    ),
                    access_token_expires_at=access_token_expires_at,
                    refresh_token_expires_at=refresh_token_expires_at,
                )
                session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def delete_tokens(self) -> None:
        async with a_session_maker() as session:
            result = await session.execute(
                select(OAuthToken).where(
                    OAuthToken.user_id == self.user_id,
                    OAuthToken.oauth_provider_id == self.oauth_provider_id,
                )
            )
            row = result.scalars().one_or_none()
            if row is not None:
                await session.delete(row)
                await session.commit()

    # ── read + refresh ─────────────────────────────────────────────────────

    async def get_raw(self) -> OAuthToken | None:
        """Return the stored row without refreshing (no lock)."""
        async with a_session_maker() as session:
            result = await session.execute(
                select(OAuthToken).where(
                    OAuthToken.user_id == self.user_id,
                    OAuthToken.oauth_provider_id == self.oauth_provider_id,
                )
            )
            return result.scalars().one_or_none()

    async def get_valid_access_token(
        self,
        permitted_drift_seconds: int = 60,
        refresh: RefreshCallback | None = None,
    ) -> str | None:
        """Return a valid access token, refreshing if necessary.

        Uses a double-checked locking pattern:

        1. Fast path — read without a lock. If the access token is still valid
           (``None`` expiry == never expires, or not yet within drift), return
           it decrypted.
        2. Slow path — the access token is expired. Set ``lock_timeout`` and
           acquire ``SELECT ... FOR UPDATE``.
        3. Double-check — another worker may have refreshed while we waited;
           if the row is now valid, return it.
        4. Refresh — call ``refresh`` (if provided) with the decrypted refresh
           token and persist the new pair.

        Args:
            permitted_drift_seconds: Clock-drift margin. An access token whose
                expiry is within this many seconds of now is treated as
                expired so a refresh starts before the IDP rejects it.
            refresh: Callback that performs the refresh and returns the new
                token pair + expiry datetimes. ``None`` skips refresh.

        Returns:
            The decrypted access token, or ``None`` if no token row exists.

        Raises:
            TokenRefreshError: If the lock cannot be acquired within the
                timeout (another worker holds it too long). Callers should
                return 401 to prompt re-authentication.
        """
        drift = timedelta(seconds=permitted_drift_seconds)

        # 1. Fast path
        async with a_session_maker() as session:
            result = await session.execute(
                select(OAuthToken).where(
                    OAuthToken.user_id == self.user_id,
                    OAuthToken.oauth_provider_id == self.oauth_provider_id,
                )
            )
            row = result.scalars().one_or_none()
            if row is None:
                return None
            if not _is_expired(row.access_token_expires_at, drift):
                return unwrap_token(row.access_token)

        # 2. Slow path — needs refresh
        try:
            async with a_session_maker() as session:
                async with session.begin():
                    await session.execute(
                        text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_SECONDS}s'")
                    )
                    result = await session.execute(
                        select(OAuthToken)
                        .where(
                            OAuthToken.user_id == self.user_id,
                            OAuthToken.oauth_provider_id == self.oauth_provider_id,
                        )
                        .with_for_update()
                    )
                    row = result.scalars().one_or_none()
                    if row is None:
                        return None

                    # 3. Double-check
                    if not _is_expired(row.access_token_expires_at, drift):
                        logger.debug(
                            'oauth_token refreshed by another worker while '
                            'waiting for lock'
                        )
                        return unwrap_token(row.access_token)

                    if refresh is None:
                        # No refresh callback: return what we have even though
                        # it is expired.
                        return unwrap_token(row.access_token)

                    refresh_token = unwrap_token(row.refresh_token)
                    if refresh_token is None:
                        return unwrap_token(row.access_token)

                    # 4. Refresh
                    refreshed = await refresh(
                        refresh_token,
                        row.access_token_expires_at,
                        row.refresh_token_expires_at,
                    )
                    if refreshed is None:
                        return unwrap_token(row.access_token)

                    row.access_token = wrap_token(str(refreshed['access_token']))
                    new_refresh = refreshed.get('refresh_token')
                    row.refresh_token = (
                        wrap_token(str(new_refresh)) if new_refresh else None
                    )
                    row.access_token_expires_at = refreshed['access_token_expires_at']
                    row.refresh_token_expires_at = refreshed.get(
                        'refresh_token_expires_at'
                    )
                    return str(refreshed['access_token'])
        except OperationalError as exc:
            logger.warning(
                'oauth_token refresh lock timeout for user %s provider %s: %s',
                self.user_id,
                self.oauth_provider_id,
                exc,
            )
            raise TokenRefreshError(
                'Unable to refresh token due to lock timeout. Please try again.'
            ) from exc

    @classmethod
    def for_session(
        cls, session: AsyncSession, user_id: UUID, oauth_provider_id: int
    ) -> _ScopedTokenStore:
        """Return a store bound to a caller-managed session (for tests)."""
        return _ScopedTokenStore(session, user_id, oauth_provider_id)


class _ScopedTokenStore:
    """Token operations scoped to a caller-managed ``AsyncSession`` (tests)."""

    def __init__(
        self, session: AsyncSession, user_id: UUID, oauth_provider_id: int
    ) -> None:
        self._session = session
        self.user_id = user_id
        self.oauth_provider_id = oauth_provider_id

    async def store_tokens(
        self,
        access_token: str,
        refresh_token: str | None,
        access_token_expires_at: datetime | None,
        refresh_token_expires_at: datetime | None,
    ) -> OAuthToken:
        result = await self._session.execute(
            select(OAuthToken).where(
                OAuthToken.user_id == self.user_id,
                OAuthToken.oauth_provider_id == self.oauth_provider_id,
            )
        )
        row = result.scalars().one_or_none()
        if row is not None:
            row.access_token = wrap_token(access_token)
            row.refresh_token = wrap_token(refresh_token) if refresh_token else None
            row.access_token_expires_at = access_token_expires_at
            row.refresh_token_expires_at = refresh_token_expires_at
        else:
            row = OAuthToken(
                user_id=self.user_id,
                oauth_provider_id=self.oauth_provider_id,
                access_token=wrap_token(access_token),
                refresh_token=(wrap_token(refresh_token) if refresh_token else None),
                access_token_expires_at=access_token_expires_at,
                refresh_token_expires_at=refresh_token_expires_at,
            )
            self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def get_raw(self) -> OAuthToken | None:
        result = await self._session.execute(
            select(OAuthToken).where(
                OAuthToken.user_id == self.user_id,
                OAuthToken.oauth_provider_id == self.oauth_provider_id,
            )
        )
        return result.scalars().one_or_none()

    async def delete_tokens(self) -> None:
        result = await self._session.execute(
            select(OAuthToken).where(
                OAuthToken.user_id == self.user_id,
                OAuthToken.oauth_provider_id == self.oauth_provider_id,
            )
        )
        row = result.scalars().one_or_none()
        if row is not None:
            await self._session.delete(row)
            await self._session.commit()

    async def get_valid_access_token(
        self,
        permitted_drift_seconds: int = 60,
        refresh: RefreshCallback | None = None,
    ) -> str | None:
        drift = timedelta(seconds=permitted_drift_seconds)
        row = await self.get_raw()
        if row is None:
            return None
        if not _is_expired(row.access_token_expires_at, drift):
            return unwrap_token(row.access_token)
        if refresh is None:
            return unwrap_token(row.access_token)
        refresh_token = unwrap_token(row.refresh_token)
        if refresh_token is None:
            return unwrap_token(row.access_token)
        refreshed = await refresh(
            refresh_token,
            row.access_token_expires_at,
            row.refresh_token_expires_at,
        )
        if refreshed is None:
            return unwrap_token(row.access_token)
        await self.store_tokens(
            str(refreshed['access_token']),
            str(refreshed['refresh_token']) if refreshed.get('refresh_token') else None,
            refreshed['access_token_expires_at'],  # type: ignore[arg-type]
            refreshed.get('refresh_token_expires_at'),  # type: ignore[arg-type]
        )
        return str(refreshed['access_token'])
