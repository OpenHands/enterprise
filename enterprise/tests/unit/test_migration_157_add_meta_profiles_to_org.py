from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '157_add_meta_profiles_to_org.py'
)
spec = spec_from_file_location('migration_157', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_157 = module_from_spec(spec)
spec.loader.exec_module(migration_157)


def test_revision_chains_off_budget_snapshot_migration():
    assert migration_157.revision == '157'
    assert migration_157.down_revision == '156'


def test_upgrade_and_downgrade(monkeypatch):
    calls = []

    class Op:
        def add_column(self, table, column):
            calls.append(('add', table, column.name, column.nullable))

        def drop_column(self, table, name):
            calls.append(('drop', table, name))

    monkeypatch.setattr(migration_157, 'op', Op())
    migration_157.upgrade()
    migration_157.downgrade()

    assert calls == [
        ('add', 'org', 'meta_profiles', True),
        ('drop', 'org', 'meta_profiles'),
    ]
