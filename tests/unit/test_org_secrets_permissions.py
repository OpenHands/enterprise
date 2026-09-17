"""Tests for the MANAGE_ORG_SECRETS permission and scope model."""

from openhands.app_server.secrets.secrets_models import (
    CustomSecretScope,
    CustomSecretWithoutValue,
)
from server.auth.authorization import (
    Permission,
    RoleName,
    get_role_permissions,
)


class TestManageOrgSecretsPermission:
    """Verify MANAGE_ORG_SECRETS is granted only to admin and owner."""

    def test_owner_has_permission(self):
        perms = get_role_permissions(RoleName.OWNER.value)
        assert Permission.MANAGE_ORG_SECRETS in perms

    def test_admin_has_permission(self):
        perms = get_role_permissions(RoleName.ADMIN.value)
        assert Permission.MANAGE_ORG_SECRETS in perms

    def test_member_does_not_have_permission(self):
        perms = get_role_permissions(RoleName.MEMBER.value)
        assert Permission.MANAGE_ORG_SECRETS not in perms

    def test_member_still_has_manage_secrets(self):
        """Members retain MANAGE_SECRETS for their personal secrets."""
        perms = get_role_permissions(RoleName.MEMBER.value)
        assert Permission.MANAGE_SECRETS in perms


class TestCustomSecretScope:
    """Verify the scope enum and model field."""

    def test_scope_values(self):
        assert CustomSecretScope.PERSONAL.value == 'personal'
        assert CustomSecretScope.ORGANIZATION.value == 'organization'

    def test_default_scope_is_personal(self):
        """CustomSecretWithoutValue defaults to personal scope."""
        secret = CustomSecretWithoutValue(name='MY_SECRET')
        assert secret.scope == CustomSecretScope.PERSONAL

    def test_scope_can_be_set_to_organization(self):
        secret = CustomSecretWithoutValue(
            name='ORG_SECRET',
            scope=CustomSecretScope.ORGANIZATION,
        )
        assert secret.scope == CustomSecretScope.ORGANIZATION

    def test_scope_in_model_dump(self):
        secret = CustomSecretWithoutValue(
            name='SECRET',
            scope=CustomSecretScope.ORGANIZATION,
        )
        dumped = secret.model_dump()
        assert dumped['scope'] == 'organization'
