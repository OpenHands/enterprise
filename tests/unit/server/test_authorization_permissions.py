from pathlib import Path


def _role_block(source: str, role_name: str) -> str:
    role_permissions = source.split(
        'ROLE_PERMISSIONS: dict[RoleName, frozenset[Permission]] = {', 1
    )[1].split('# Instance-level permissions', 1)[0]
    return role_permissions.split(f'    {role_name}: frozenset(', 1)[1].split(
        '    ),', 1
    )[0]


def test_automation_roles_include_expected_permissions():
    source = Path('enterprise/server/auth/authorization.py').read_text()
    assert "VIEW_AUTOMATIONS = 'view_automations'" in source

    for role_name in ('RoleName.OWNER', 'RoleName.ADMIN'):
        block = _role_block(source, role_name)
        assert 'Permission.VIEW_AUTOMATIONS' in block
        assert 'Permission.MANAGE_AUTOMATIONS' in block

    member_block = _role_block(source, 'RoleName.MEMBER')
    assert 'Permission.VIEW_AUTOMATIONS' in member_block
    assert 'Permission.MANAGE_AUTOMATIONS' not in member_block
