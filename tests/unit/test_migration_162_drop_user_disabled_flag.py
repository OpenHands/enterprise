from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '162_drop_user_disabled_flag.py'
)
spec = spec_from_file_location('migration_162', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_162 = module_from_spec(spec)
spec.loader.exec_module(migration_162)


def test_upgrade_drops_user_disabled_flag_when_present(monkeypatch):
    bind = MagicMock()
    op = MagicMock()
    op.get_bind.return_value = bind
    inspector = MagicMock()
    inspector.get_columns.return_value = [{'name': 'id'}, {'name': 'is_disabled'}]
    monkeypatch.setattr(migration_162, 'op', op)
    monkeypatch.setattr(migration_162.sa, 'inspect', MagicMock(return_value=inspector))

    migration_162.upgrade()

    inspector.get_columns.assert_called_once_with('user')
    op.drop_column.assert_called_once_with('user', 'is_disabled')


def test_upgrade_is_noop_when_user_disabled_flag_is_absent(monkeypatch):
    op = MagicMock()
    inspector = MagicMock()
    inspector.get_columns.return_value = [{'name': 'id'}]
    monkeypatch.setattr(migration_162, 'op', op)
    monkeypatch.setattr(migration_162.sa, 'inspect', MagicMock(return_value=inspector))

    migration_162.upgrade()

    op.drop_column.assert_not_called()
