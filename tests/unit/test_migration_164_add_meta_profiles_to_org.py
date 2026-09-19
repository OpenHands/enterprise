from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '164_add_meta_profiles_to_org.py'
)
spec = spec_from_file_location('migration_164', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_164 = module_from_spec(spec)
spec.loader.exec_module(migration_164)


def test_revision_chains_off_budget_snapshot_migration():
    assert migration_164.revision == '164'
    assert migration_164.down_revision == '163'


def test_upgrade_and_downgrade(monkeypatch):
    calls = []

    class Op:
        def add_column(self, table, column):
            calls.append(('add', table, column.name, column.nullable))

        def drop_column(self, table, name):
            calls.append(('drop', table, name))

    monkeypatch.setattr(migration_164, 'op', Op())
    migration_164.upgrade()
    migration_164.downgrade()

    assert calls == [
        ('add', 'org', 'meta_profiles', True),
        ('drop', 'org', 'meta_profiles'),
    ]
