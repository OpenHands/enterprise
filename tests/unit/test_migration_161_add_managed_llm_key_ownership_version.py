from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '161_add_managed_llm_key_ownership_version.py'
)
spec = spec_from_file_location('migration_161', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_161 = module_from_spec(spec)
spec.loader.exec_module(migration_161)


def test_upgrade_marks_legacy_rows_stale_and_new_rows_current(monkeypatch):
    calls = []

    class Op:
        def add_column(self, table, column):
            calls.append(('add', table, column))

        def execute(self, statement):
            calls.append(('execute', str(statement)))

        def alter_column(self, table, column, **kwargs):
            calls.append(('alter', table, column, kwargs))

    monkeypatch.setattr(migration_161, 'op', Op())
    migration_161.upgrade()

    operation, table, column = calls[0]
    assert operation == 'add'
    assert table == 'org_member'
    assert column.name == 'managed_llm_key_ownership_version'
    assert column.nullable
    assert calls[1] == (
        'execute',
        'UPDATE org_member SET managed_llm_key_ownership_version = 0',
    )
    operation, table, column_name, kwargs = calls[2]
    assert operation == 'alter'
    assert table == 'org_member'
    assert column_name == 'managed_llm_key_ownership_version'
    assert kwargs['nullable'] is False
    assert str(kwargs['server_default']) == '1'


def test_downgrade_drops_ownership_version(monkeypatch):
    dropped = []

    class Op:
        def drop_column(self, table, column):
            dropped.append((table, column))

    monkeypatch.setattr(migration_161, 'op', Op())
    migration_161.downgrade()

    assert dropped == [('org_member', 'managed_llm_key_ownership_version')]
