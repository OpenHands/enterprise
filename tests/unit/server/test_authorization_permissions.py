from pathlib import Path


def test_automation_roles_include_view_and_manage_permissions():
    source = Path('enterprise/server/auth/authorization.py').read_text()
    assert "VIEW_AUTOMATIONS = 'view_automations'" in source

    role_block = source.split(
        'ROLE_PERMISSIONS: dict[RoleName, frozenset[Permission]] = {', 1
    )[1].split('# Instance-level permissions', 1)[0]
    for role_name in ('RoleName.OWNER', 'RoleName.ADMIN', 'RoleName.MEMBER'):
        block = role_block.split(f'    {role_name}: frozenset(', 1)[1].split(
            '    ),', 1
        )[0]
        assert 'Permission.VIEW_AUTOMATIONS' in block
        assert 'Permission.MANAGE_AUTOMATIONS' in block
