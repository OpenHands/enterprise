from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.auth import mode
from server.auth.contracts import InvalidCredentials, SessionExpired
from server.auth.local.accounts import AccountConflict, create_local_account
from server.auth.local.actions import InvalidActionToken, LocalAccountActions
from server.auth.local.adapter import LocalUserDatabase
from server.auth.local.bootstrap import bootstrap_local_admin
from server.auth.local.credentials import LocalPasswordCredentialService
from server.auth.local.passwords import PasswordPolicyError, validate_password
from server.auth.local.recover_admin import recover_admin
from server.auth.local.sessions import LocalBrowserSessionBackend, token_digest
from server.auth.mode import AuthenticationConfigurationError, initialize_authentication
from storage.auth_action_tokens import AuthActionToken
from storage.auth_sessions import AuthSession
from storage.installation_auth import InstallationAuth
from storage.local_credentials import LocalCredentials
from storage.org import Org
from storage.org_invitation import OrgInvitation
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User
from tests.unit.auth_schema import create_auth_schema

PASSWORD = SecretStr('A valid password with spaces')
NEW_PASSWORD = SecretStr('A different password with spaces')


@pytest.fixture
async def local_db(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(create_auth_schema)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        session.add_all(
            [
                Role(id=1, name='owner', rank=1),
                Role(id=2, name='admin', rank=2),
                Role(id=3, name='member', rank=3),
            ]
        )
    monkeypatch.setattr(mode, '_auth_mode', None)
    yield factory
    await engine.dispose()


async def account(factory, email='person@example.com', **kwargs):
    async with factory() as session, session.begin():
        return await create_local_account(session, email, PASSWORD, **kwargs)


async def test_bootstrap_graph_and_restart_has_no_env_effects(local_db, monkeypatch):
    monkeypatch.setenv('OH_BOOTSTRAP_ADMIN_EMAIL', ' Admin+Tag@EXAMPLE.com ')
    monkeypatch.setenv('OH_BOOTSTRAP_ADMIN_PASSWORD', PASSWORD.get_secret_value())
    await initialize_authentication(
        session_factory=local_db, environ={}, bootstrap=bootstrap_local_admin
    )
    async with local_db() as session, session.begin():
        installation = await session.get(InstallationAuth, 1)
        user = await session.get(User, installation.bootstrap_admin_id)
        assert user.id == user.current_org_id
        assert user.role_id == 2
        assert user.email == 'admin+tag@example.com'
        assert not user.email_verified
        member = await session.get(OrgMember, (user.id, user.id))
        assert member.role_id == 1
        credential = await session.get(LocalCredentials, user.id)
        assert credential.must_change_password
        initial_hash = credential.password_hash
        user.role_id = None
    monkeypatch.setenv('OH_BOOTSTRAP_ADMIN_EMAIL', 'other@example.com')
    monkeypatch.setenv('OH_BOOTSTRAP_ADMIN_PASSWORD', 'invalid')
    await initialize_authentication(
        session_factory=local_db, environ={}, bootstrap=bootstrap_local_admin
    )
    async with local_db() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert (await session.get(User, user.id)).role_id is None
        assert (
            await session.get(LocalCredentials, user.id)
        ).password_hash == initial_hash


async def test_bootstrap_invalid_input_rolls_back(local_db, monkeypatch):
    monkeypatch.setenv('OH_BOOTSTRAP_ADMIN_EMAIL', 'admin@example.com')
    monkeypatch.setenv('OH_BOOTSTRAP_ADMIN_PASSWORD', 'short')
    with pytest.raises(AuthenticationConfigurationError):
        await initialize_authentication(
            session_factory=local_db, environ={}, bootstrap=bootstrap_local_admin
        )
    async with local_db() as session:
        assert await session.get(InstallationAuth, 1) is None
        assert await session.scalar(select(func.count()).select_from(User)) == 0


async def test_ordinary_first_account_has_no_super_role(local_db):
    user = await account(local_db)
    assert user.role_id is None
    async with local_db() as session:
        projected = await LocalUserDatabase(session).get(user.id)
        assert not projected.is_superuser
        assert projected.is_active
        assert (await session.get(Org, user.id)).agent_settings


async def test_default_llm_key_is_only_persisted_in_encrypted_member_column(
    local_db, monkeypatch
):
    from openhands.app_server.settings.settings_models import Settings
    from storage.user_store import UserStore

    settings = Settings(language='en')
    key = 'synthetic-default-llm-secret'
    settings.update({'agent_settings_diff': {'llm': {'api_key': key}}})
    monkeypatch.setattr(UserStore, 'default_settings', lambda: settings)
    user = await account(local_db)
    async with local_db() as session:
        org = await session.get(Org, user.id)
        member = await session.get(OrgMember, (user.id, user.id))
        assert 'api_key' not in org.agent_settings['llm']
        assert key not in str(org.agent_settings)
        assert key not in member._llm_api_key
        assert member.llm_api_key.get_secret_value() == key


async def test_normalization_is_unique_and_preserves_plus_dots(local_db):
    await account(local_db, 'P.erson+tag@EXAMPLE.com')
    with pytest.raises(AccountConflict):
        await account(local_db, ' p.erson+tag@example.com ')
    await account(local_db, 'person+tag@example.com')


@pytest.mark.parametrize('password', ['x' * 15, 'x' * 1024, '🙂' * 15, ' ' * 15])
def test_password_policy_accepts_unicode_and_whitespace(password):
    validate_password(SecretStr(password))


@pytest.mark.parametrize('password', ['', 'x' * 14, 'x' * 1025])
def test_password_policy_rejects_length_only(password):
    with pytest.raises(PasswordPolicyError):
        validate_password(SecretStr(password))


async def test_login_unknown_disabled_and_malformed_hash_are_generic(local_db):
    user = await account(local_db)
    passwords = LocalPasswordCredentialService(local_db)
    for email, password in [
        ('missing@example.com', PASSWORD),
        (user.email, NEW_PASSWORD),
    ]:
        with pytest.raises(InvalidCredentials):
            await passwords.verify(email, password)
    async with local_db() as session, session.begin():
        await session.execute(
            update(User).where(User.id == user.id).values(is_disabled=True)
        )
    with pytest.raises(InvalidCredentials):
        await passwords.verify(user.email, PASSWORD)
    async with local_db() as session, session.begin():
        await session.execute(
            update(User).where(User.id == user.id).values(is_disabled=False)
        )
        await session.execute(
            update(LocalCredentials)
            .where(LocalCredentials.user_id == user.id)
            .values(password_hash='malformed')
        )
    with pytest.raises(InvalidCredentials):
        await passwords.verify(user.email, PASSWORD)


async def test_sessions_digest_expiry_restriction_and_logout(local_db):
    user = await account(local_db)
    passwords = LocalPasswordCredentialService(local_db)
    backend = LocalBrowserSessionBackend(local_db)
    issued = await passwords.login(user.email, PASSWORD)
    assert issued.principal.restricted
    assert (issued.expires_at - issued.principal.authenticated_at) == timedelta(
        hours=24
    )
    async with local_db() as session:
        stored = await session.get(AuthSession, token_digest(issued.token))
        assert stored.token_digest != issued.token.get_secret_value()
    assert (await backend.validate(issued.token)).user_id == user.id
    await backend.revoke(issued.token)
    with pytest.raises(SessionExpired):
        await backend.validate(issued.token)
    expired = await backend.issue(user.id)
    async with local_db() as session, session.begin():
        record = await session.get(AuthSession, token_digest(expired.token))
        record.created_at = datetime.now(UTC) - timedelta(days=2)
        record.expires_at = datetime.now(UTC) - timedelta(days=1)
    with pytest.raises(SessionExpired):
        await backend.validate(expired.token)


async def test_password_change_rotates_all_sessions_and_rechecks_current_state(
    local_db,
):
    user = await account(local_db)
    passwords = LocalPasswordCredentialService(local_db)
    backend = LocalBrowserSessionBackend(local_db)
    old = await backend.issue(user.id)
    other = await backend.issue(user.id)
    new = await passwords.change_and_issue(user.id, PASSWORD, NEW_PASSWORD)
    assert not new.principal.restricted
    for issued in (old, other):
        with pytest.raises(SessionExpired):
            await backend.validate(issued.token)
    async with local_db() as session, session.begin():
        credential = await session.get(LocalCredentials, user.id)
        credential.must_change_password = True
    assert (await backend.validate(new.token)).restricted
    async with local_db() as session, session.begin():
        user = await session.get(User, user.id)
        user.is_disabled = True
    with pytest.raises(SessionExpired):
        await backend.validate(new.token)


async def test_reset_token_purpose_replay_and_session_revocation(local_db):
    user = await account(local_db, must_change_password=False)
    actions = LocalAccountActions(local_db)
    backend = LocalBrowserSessionBackend(local_db)
    issued = await backend.issue(user.id)
    email = await actions.request_reset(user.email)
    async with local_db() as session:
        token = await session.get(AuthActionToken, token_digest(email.token))
        assert token.expires_at - token.created_at == timedelta(hours=1)
    with pytest.raises(InvalidActionToken):
        await actions.verify_email(email.token)
    await actions.reset_password(email.token, NEW_PASSWORD)
    with pytest.raises(InvalidActionToken):
        await actions.reset_password(email.token, PASSWORD)
    with pytest.raises(SessionExpired):
        await backend.validate(issued.token)
    assert (
        await LocalPasswordCredentialService(local_db).verify(user.email, NEW_PASSWORD)
    ).id == user.id


async def test_email_replacement_is_atomic_and_invalidates_old_actions(local_db):
    user = await account(local_db, must_change_password=False)
    actions = LocalAccountActions(local_db)
    reset = await actions.request_reset(user.email)
    change = await actions.request_verification(user.id, 'new@example.com')
    backend = LocalBrowserSessionBackend(local_db)
    old_session = await backend.issue(user.id)
    async with local_db() as session:
        assert (await session.get(User, user.id)).email == user.email
        assert (
            await session.get(LocalCredentials, user.id)
        ).normalized_email == user.email
    await actions.verify_email(change.token)
    with pytest.raises(InvalidActionToken):
        await actions.reset_password(reset.token, NEW_PASSWORD)
    with pytest.raises(SessionExpired):
        await backend.validate(old_session.token)
    async with local_db() as session:
        changed = await session.get(User, user.id)
        assert changed.email == 'new@example.com'
        assert changed.email_verified
        assert (
            await session.get(LocalCredentials, user.id)
        ).normalized_email == changed.email


async def test_email_conflict_rolls_back_token_and_profile(local_db):
    user = await account(local_db, must_change_password=False)
    await account(local_db, 'other@example.com')
    actions = LocalAccountActions(local_db)
    change = await actions.request_verification(user.id, 'other@example.com')
    with pytest.raises(InvalidActionToken):
        await actions.verify_email(change.token)
    async with local_db() as session:
        assert (await session.get(User, user.id)).email == user.email
        assert (
            await session.get(AuthActionToken, token_digest(change.token))
        ).consumed_at is None


async def invitation(
    factory, owner, email='invited@example.com', token='invite-secret'
):
    async with factory() as session, session.begin():
        invite = OrgInvitation(
            token=token,
            org_id=owner.id,
            email=email,
            role_id=2,
            inviter_id=owner.id,
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1),
        )
        session.add(invite)
        await session.flush()
        return invite


async def test_enrollment_proves_email_preserves_role_and_prevents_replay(local_db):
    owner = await account(local_db)
    invite = await invitation(local_db, owner)
    actions = LocalAccountActions(local_db)
    enrolled = await actions.enroll(SecretStr(invite.token), PASSWORD)
    async with local_db() as session:
        user = await session.get(User, enrolled.principal.user_id)
        assert user.email == invite.email and user.email_verified
        assert user.role_id is None
        assert user.current_org_id == owner.id
        assert (
            await session.get(OrgMember, (owner.id, user.id))
        ).role_id == invite.role_id
        assert (
            await session.get(OrgInvitation, invite.id)
        ).accepted_by_user_id == user.id
    with pytest.raises(InvalidActionToken):
        await actions.enroll(SecretStr(invite.token), PASSWORD)


async def test_enrollment_cannot_take_over_existing_email(local_db):
    owner = await account(local_db)
    invite = await invitation(local_db, owner, owner.email)
    with pytest.raises(AccountConflict):
        await LocalAccountActions(local_db).enroll(
            SecretStr(invite.token), NEW_PASSWORD
        )
    assert (
        await LocalPasswordCredentialService(local_db).verify(owner.email, PASSWORD)
    ).id == owner.id


async def test_operator_recovery_only_existing_active_instance_admin(local_db):
    user = await account(local_db, instance_admin=True)
    async with local_db() as session, session.begin():
        session.add(
            InstallationAuth(
                id=1, mode='local', bootstrap_admin_id=user.id, bootstrap_complete=True
            )
        )
    backend = LocalBrowserSessionBackend(local_db)
    old = await backend.issue(user.id)
    await recover_admin(user.email, NEW_PASSWORD, session_factory=local_db)
    with pytest.raises(SessionExpired):
        await backend.validate(old.token)
    async with local_db() as session, session.begin():
        await session.execute(
            update(User).where(User.id == user.id).values(role_id=None)
        )
    with pytest.raises(ValueError):
        await recover_admin(user.email, PASSWORD, session_factory=local_db)


async def test_password_change_failure_keeps_credentials_and_sessions(local_db):
    user = await account(local_db, must_change_password=False)
    passwords = LocalPasswordCredentialService(local_db)
    backend = LocalBrowserSessionBackend(local_db)
    issued = await backend.issue(user.id)
    with pytest.raises(InvalidCredentials):
        await passwords.change(
            user.id, NEW_PASSWORD, SecretStr('another valid password')
        )
    with pytest.raises(PasswordPolicyError):
        await passwords.change(user.id, PASSWORD, SecretStr('short'))
    assert (await backend.validate(issued.token)).user_id == user.id
    assert (await passwords.verify(user.email, PASSWORD)).id == user.id


async def test_expired_action_and_superseded_verification_fail(local_db):
    user = await account(local_db, must_change_password=False)
    actions = LocalAccountActions(local_db)
    first = await actions.request_verification(user.id)
    second = await actions.request_verification(user.id, 'replacement@example.com')
    with pytest.raises(InvalidActionToken):
        await actions.verify_email(first.token)
    async with local_db() as session, session.begin():
        record = await session.get(AuthActionToken, token_digest(second.token))
        assert record.expires_at - record.created_at == timedelta(hours=24)
        record.created_at = datetime.now(UTC) - timedelta(days=2)
        record.expires_at = datetime.now(UTC) - timedelta(days=1)
    with pytest.raises(InvalidActionToken):
        await actions.verify_email(second.token)
    async with local_db() as session:
        assert (await session.get(User, user.id)).email == user.email


async def test_expired_invitation_cannot_create_account(local_db):
    owner = await account(local_db)
    invite = await invitation(local_db, owner)
    async with local_db() as session, session.begin():
        await session.execute(
            update(OrgInvitation)
            .where(OrgInvitation.id == invite.id)
            .values(
                expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
            )
        )
    with pytest.raises(InvalidActionToken):
        await LocalAccountActions(local_db).enroll(SecretStr(invite.token), PASSWORD)
    async with local_db() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1


async def test_recovery_never_initializes_installation(local_db):
    await account(local_db, instance_admin=True)
    with pytest.raises(AuthenticationConfigurationError):
        await recover_admin(
            'person@example.com', NEW_PASSWORD, session_factory=local_db
        )
    async with local_db() as session:
        assert await session.get(InstallationAuth, 1) is None


async def test_library_adapter_only_exposes_existing_identity_and_hash_upgrade(
    local_db,
):
    user = await account(local_db, instance_admin=True)
    async with local_db() as session, session.begin():
        adapter = LocalUserDatabase(session)
        projected = await adapter.get(user.id)
        assert projected.id == user.id and projected.is_superuser
        with pytest.raises(ValueError):
            await adapter.create({'email': 'other@example.com'})
        with pytest.raises(ValueError):
            await adapter.update(projected, {'is_superuser': True})
        with pytest.raises(ValueError):
            await adapter.delete(projected)
        await session.execute(
            update(User).where(User.id == user.id).values(role_id=None)
        )
        assert not (await adapter.get(user.id)).is_superuser
