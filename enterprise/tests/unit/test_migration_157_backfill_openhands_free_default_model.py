from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '157_backfill_openhands_free_default_model.py'
)
spec = spec_from_file_location('migration_157', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_157 = module_from_spec(spec)
spec.loader.exec_module(migration_157)


def test_upgrade_only_backfills_verified_models(monkeypatch):
    statements = []
    monkeypatch.setattr(
        migration_157.op, 'execute', lambda statement: statements.append(str(statement))
    )

    migration_157.upgrade()

    rendered = '\n'.join(statements)
    assert 'verified_models' in rendered
    assert 'org_member' not in rendered
    assert 'llm_profiles' not in rendered
    assert 'agent_settings' not in rendered
