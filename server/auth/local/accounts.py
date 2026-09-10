"""Persist the existing OpenHands account graph in a caller-owned transaction."""

from uuid import uuid4

from email_validator import EmailNotValidError, validate_email
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.local.passwords import hash_password
from server.auth.mode import AuthenticationConfigurationError
from storage.local_credentials import LocalCredentials, normalize_login_email
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User


class AccountConflict(ValueError):
    """The login email is already assigned to an account."""


def validated_email(email: str) -> str:
    normalized = normalize_login_email(email)
    try:
        validate_email(normalized, check_deliverability=False)
    except EmailNotValidError:
        raise ValueError('Enter a valid email address.') from None
    if len(normalized) > 320:
        raise ValueError('Enter a valid email address.')
    return normalized


async def create_local_account(
    session: AsyncSession,
    email: str,
    password: SecretStr,
    *,
    must_change_password: bool = True,
    instance_admin: bool = False,
    email_verified: bool = False,
    first_name: str | None = None,
    last_name: str | None = None,
) -> User:
    """Create a personal org, owner membership, user and credential atomically.

    The caller commits. No remote provisioning or implicit first-user grant is
    allowed here; bootstrap explicitly requests the instance administrator role.
    """
    email = validated_email(email)
    if await session.scalar(
        select(LocalCredentials.user_id).where(
            LocalCredentials.normalized_email == email
        )
    ):
        raise AccountConflict('An account already uses this email address.')
    owner = await session.scalar(select(Role).where(Role.name == 'owner'))
    admin = await session.scalar(select(Role).where(Role.name == 'admin'))
    if owner is None or (instance_admin and admin is None):
        raise AuthenticationConfigurationError(
            'Required roles are missing; run database migrations.'
        )
    password_hash = await hash_password(password)
    from storage.user_store import UserStore

    settings = UserStore.default_settings()
    user_id = uuid4()
    # The LLM key belongs in the encrypted member column. Org.agent_settings is
    # ordinary JSON, so never duplicate a deployment-provided key into that blob.
    dumped = settings.model_dump(mode='json', context={'expose_secrets': True})
    dumped.get('agent_settings', {}).get('llm', {}).pop('api_key', None)
    org_kwargs = {
        key: dumped[key]
        for column in Org.__table__.columns
        if (key := column.name.lstrip('_')) in dumped
        and key not in ('id', 'name', 'contact_name', 'contact_email')
    }
    org = Org(
        id=user_id,
        name=f'user_{user_id}_org',
        contact_email=email,
        contact_name=' '.join(part for part in (first_name, last_name) if part)
        or email,
        **org_kwargs,
    )
    session.add(org)
    await session.flush()
    user_kwargs = {
        key: getattr(settings, key)
        for column in User.__table__.columns
        if hasattr(settings, key := column.name.lstrip('_'))
        and key not in ('id', 'current_org_id', 'role_id', 'email', 'email_verified')
    }
    user = User(
        id=user_id,
        current_org_id=user_id,
        role_id=admin.id if instance_admin and admin else None,
        email=email,
        email_verified=email_verified,
        is_disabled=False,
        **user_kwargs,
    )
    session.add(user)
    await session.flush()
    session.add(
        OrgMember(
            org_id=user_id,
            user_id=user_id,
            role_id=owner.id,
            status='active',
            llm_api_key=settings.agent_settings.llm.api_key or SecretStr(''),
            agent_settings_diff={},
            conversation_settings_diff={},
        )
    )
    session.add(
        LocalCredentials(
            user_id=user_id,
            normalized_email=email,
            password_hash=password_hash,
            must_change_password=must_change_password,
        )
    )
    await session.flush()
    return user
