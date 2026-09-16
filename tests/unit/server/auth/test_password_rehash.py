"""FastAPI Users upgrades existing Argon2 credentials during login."""

from argon2 import PasswordHasher
from fastapi_users.password import PasswordHelper

from server.auth.native_types import SessionFactory
from storage.native_auth import PasswordCredential
from tests.unit.server.auth.native_test_types import NativeFixture, present

PASSWORD = ' A long Pássword with spaces 987! '


async def test_login_persists_library_hash_upgrade(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, account_id = native
    password = 'old-short'
    original = PasswordHasher(time_cost=2, memory_cost=32768, parallelism=1).hash(
        password
    )
    async with configured() as session, session.begin():
        credential = present(await session.get(PasswordCredential, account_id))
        credential.password_hash = original
    login = await service.login(' ADMIN@EXAMPLE.COM ', password, client_ip='upgrade')
    assert login.principal.account_id == account_id
    async with configured() as session:
        credential = present(await session.get(PasswordCredential, account_id))
        assert credential.password_hash != original
        assert PasswordHelper().verify_and_update(
            password, credential.password_hash
        ) == (
            True,
            None,
        )


pytest_plugins = ['tests.unit.server.auth.test_native_account_provisioning']
