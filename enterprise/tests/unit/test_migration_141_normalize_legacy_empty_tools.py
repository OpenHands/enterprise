from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '141_normalize_legacy_empty_tools.py'
)
spec = spec_from_file_location('migration_141', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_141 = module_from_spec(spec)
spec.loader.exec_module(migration_141)


def test_postgresql_updates_json_in_place():
    statements = [
        migration_141._empty_tools_update(table, column)
        for table, column in (
            ('user_settings', 'agent_settings'),
            ('org', 'agent_settings'),
            ('org_member', 'agent_settings_diff'),
        )
    ]

    compiled = [
        str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={'literal_binds': True},
            )
        )
        for statement in statements
    ]

    assert all(statement.startswith('UPDATE ') for statement in compiled)
    assert all('jsonb_set(' in statement for statement in compiled)
    assert all("['tools'] = CAST('[]' AS JSONB)" in statement for statement in compiled)
    assert all('SELECT ' not in statement for statement in compiled)


def test_upgrade_updates_each_postgresql_table(monkeypatch):
    statements = []
    bind = SimpleNamespace(
        dialect=SimpleNamespace(name='postgresql'),
        execute=statements.append,
    )
    monkeypatch.setattr(
        migration_141,
        'op',
        SimpleNamespace(get_bind=lambda: bind),
    )

    migration_141.upgrade()

    compiled = [
        str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={'literal_binds': True},
            )
        )
        for statement in statements
    ]
    assert [statement.partition(' SET ')[0] for statement in compiled] == [
        'UPDATE user_settings',
        'UPDATE org',
        'UPDATE org_member',
    ]
