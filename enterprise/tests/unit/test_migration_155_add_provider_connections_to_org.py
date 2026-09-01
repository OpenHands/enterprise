from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "155_add_provider_connections_to_org.py"
)
spec = spec_from_file_location("migration_155", MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_155 = module_from_spec(spec)
spec.loader.exec_module(migration_155)


def test_revision_chains_off_main_revision_154():
    # main already ships 154_create_feature_flags; this migration must
    # extend that chain, not fork it (alembic rejects duplicate revisions).
    assert migration_155.revision == "155"
    assert migration_155.down_revision == "154"


def test_upgrade_adds_provider_connections_column(monkeypatch):
    added = []

    class Op:
        def add_column(self, table, column):
            added.append((table, column))

    monkeypatch.setattr(migration_155, "op", Op())

    migration_155.upgrade()

    assert len(added) == 1
    table, column = added[0]
    assert table == "org"
    assert column.name == "provider_connections"
    assert column.nullable


def test_downgrade_drops_provider_connections_column(monkeypatch):
    dropped = []

    class Op:
        def drop_column(self, table, name):
            dropped.append((table, name))

    monkeypatch.setattr(migration_155, "op", Op())

    migration_155.downgrade()

    assert dropped == [("org", "provider_connections")]
