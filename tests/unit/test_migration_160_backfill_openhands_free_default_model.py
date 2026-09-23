from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '160_backfill_openhands_free_default_model.py'
)
spec = spec_from_file_location('migration_160', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_160 = module_from_spec(spec)
spec.loader.exec_module(migration_160)


def test_upgrade_only_backfills_verified_models(monkeypatch):
    statements = []
    monkeypatch.setattr(
        migration_160.op, 'execute', lambda statement: statements.append(str(statement))
    )
    monkeypatch.setenv('WEB_HOST', 'app.all-hands.dev')

    migration_160.upgrade()

    rendered = '\n'.join(statements)
    assert 'verified_models' in rendered
    assert 'org_member' not in rendered
    assert 'llm_profiles' not in rendered
    assert 'agent_settings' not in rendered
    # The free-model insert + the guarded default backfill UPDATE.
    assert rendered.count('INSERT INTO verified_models') == 1
    assert 'is_default = true' in rendered
    assert 'is_free = true' in rendered
    assert 'NOT EXISTS' in rendered


@pytest.mark.parametrize(
    'web_host',
    [
        '',
        'openhands.example.com',
        'app.all-hands.dev.attacker.com',
        'evil-app.all-hands.dev',
        'app.openhands.ai',
    ],
)
def test_upgrade_skips_backfill_on_other_deployments(monkeypatch, web_host):
    statements = []
    monkeypatch.setattr(
        migration_160.op, 'execute', lambda statement: statements.append(str(statement))
    )
    monkeypatch.setenv('WEB_HOST', web_host)
    migration_160.upgrade()
    assert statements == []


@pytest.mark.parametrize(
    'web_host',
    [
        'app.all-hands.dev',
        'staging.all-hands.dev',
        'dev.all-hands.dev',
        'pr-1.staging.all-hands.dev',
        'my-feature-branch.staging.all-hands.dev',
    ],
)
def test_upgrade_backfills_on_managed_deployments(monkeypatch, web_host):
    statements = []
    monkeypatch.setattr(
        migration_160.op, 'execute', lambda statement: statements.append(str(statement))
    )
    monkeypatch.setenv('WEB_HOST', web_host)
    migration_160.upgrade()
    assert any('INSERT INTO verified_models' in s for s in statements)
