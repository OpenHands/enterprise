"""Transaction-taking local profile and lifecycle operations.

Acquire the installation lock before any account, membership, or profile lock.
No helper commits or calls an identity, billing, or LLM service.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storage.native_auth import (
    AuthAccount,
    BrowserSession,
    PasswordCredential,
)

# Common to bootstrap, grant/revoke, disable/delete, reonboarding and recovery.
NATIVE_LIFECYCLE_LOCK = 724408037958003618


async def lock_native_lifecycle(session: AsyncSession) -> None:
    await session.execute(
        text('SELECT pg_advisory_xact_lock(:key)'), {'key': NATIVE_LIFECYCLE_LOCK}
    )


async def email_admitted(
    session: AsyncSession, email: str, auth_method: str = 'password'
) -> bool:
    """Native admission uses the authoritative email without alias rewriting."""
    from storage.user_authorization import UserAuthorizationType
    from storage.user_authorization_store import UserAuthorizationStore

    policy = await UserAuthorizationStore.get_authorization_type(
        email, auth_method, session
    )
    return policy != UserAuthorizationType.BLACKLIST


async def account_admitted(session: AsyncSession, account: AuthAccount) -> bool:
    """Non-browser authority requires an admitted password credential."""
    if (
        account.normalized_email is None
        or await session.get(PasswordCredential, account.id) is None
    ):
        return False
    return await email_admitted(session, account.normalized_email)


async def session_method_admitted(
    session: AsyncSession, account: AuthAccount, browser: BrowserSession
) -> bool:
    if account.normalized_email is None or browser.auth_method != 'password':
        return False
    credential = await session.get(PasswordCredential, account.id)
    if (
        credential is None
        or credential.credential_version != browser.credential_version
    ):
        return False
    return await email_admitted(session, account.normalized_email)
