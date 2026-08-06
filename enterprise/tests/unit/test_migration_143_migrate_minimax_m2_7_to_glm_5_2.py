from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '143_migrate_minimax_m2_7_to_glm_5_2.py'
)
spec = spec_from_file_location('migration_143', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_143 = module_from_spec(spec)
spec.loader.exec_module(migration_143)


def test_replacement_set_is_exact_and_managed_only():
    assert migration_143.MODEL_REPLACEMENTS == {
        'openhands/minimax-m2.7': 'openhands/glm-5.2',
        'litellm_proxy/minimax-m2.7': 'litellm_proxy/glm-5.2',
        'minimax-m2.7': 'glm-5.2',
    }
    assert 'anthropic/minimax-m2.7' not in migration_143.MODEL_REPLACEMENTS
    assert 'custom/minimax-m2.7' not in migration_143.MODEL_REPLACEMENTS


def test_statement_updates_nested_llm_model_by_exact_match():
    statement = migration_143._replace_llm_model_statement(
        'org_member',
        'agent_settings_diff',
    )
    sql = ' '.join(str(statement).split())

    assert sql.startswith('UPDATE org_member SET agent_settings_diff = jsonb_set(')
    assert "agent_settings_diff::jsonb, '{llm,model}'" in sql
    assert 'to_jsonb(CAST(:new_model AS text))' in sql
    assert "WHERE agent_settings_diff::jsonb #>> '{llm,model}' = :old_model" in sql


def test_upgrade_updates_each_settings_column_for_each_replacement(monkeypatch):
    calls = []

    class Result:
        def mappings(self):
            return []

    class Bind:
        dialect = SimpleNamespace(name='postgresql')

        def execute(self, statement, params=None):
            if str(statement).lstrip().startswith('SELECT '):
                return Result()
            calls.append((statement, params))
            return None

    monkeypatch.setattr(
        migration_143,
        'op',
        SimpleNamespace(get_bind=lambda: Bind()),
    )

    migration_143.upgrade()

    assert len(calls) == (
        len(migration_143.JSON_MODEL_COLUMNS) * len(migration_143.MODEL_REPLACEMENTS)
    )
    assert [params for _, params in calls[:3]] == [
        {'old_model': 'openhands/minimax-m2.7', 'new_model': 'openhands/glm-5.2'},
        {
            'old_model': 'litellm_proxy/minimax-m2.7',
            'new_model': 'litellm_proxy/glm-5.2',
        },
        {'old_model': 'minimax-m2.7', 'new_model': 'glm-5.2'},
    ]

    updated_tables = [
        ' '.join(str(statement).split()).partition(' SET ')[0]
        for statement, _ in calls
    ]
    assert updated_tables == [
        'UPDATE user_settings',
        'UPDATE user_settings',
        'UPDATE user_settings',
        'UPDATE org',
        'UPDATE org',
        'UPDATE org',
        'UPDATE org_member',
        'UPDATE org_member',
        'UPDATE org_member',
    ]


def test_replace_model_values_updates_nested_profile_models_only_on_exact_match():
    payload = {
        'profiles': {
            'managed': {'model': 'openhands/minimax-m2.7'},
            'legacy_proxy': {'model': 'litellm_proxy/minimax-m2.7'},
            'bare': {'model': 'minimax-m2.7'},
            'custom': {'model': 'anthropic/minimax-m2.7'},
        },
        'active': 'managed',
    }

    replaced, changed = migration_143._replace_model_values(payload)

    assert changed is True
    assert replaced == {
        'profiles': {
            'managed': {'model': 'openhands/glm-5.2'},
            'legacy_proxy': {'model': 'litellm_proxy/glm-5.2'},
            'bare': {'model': 'glm-5.2'},
            'custom': {'model': 'anthropic/minimax-m2.7'},
        },
        'active': 'managed',
    }
    assert payload['profiles']['managed']['model'] == 'openhands/minimax-m2.7'


def test_upgrade_encrypted_llm_profiles_reencrypts_changed_rows(monkeypatch):
    executed_updates = []
    rows = [
        {
            'id': 'user-1',
            'llm_profiles': 'encrypted-managed',
        },
        {
            'id': 'user-2',
            'llm_profiles': 'encrypted-custom',
        },
    ]

    class Result:
        def mappings(self):
            return rows

    class Bind:
        def execute(self, statement, params=None):
            if str(statement).lstrip().startswith('SELECT '):
                return Result()
            executed_updates.append((statement, params))
            return None

    def fake_decrypt(value):
        if value == 'encrypted-managed':
            return {
                'profiles': {
                    'Default': {'model': 'openhands/minimax-m2.7'},
                },
                'active': 'Default',
            }
        return {
            'profiles': {
                'Custom': {'model': 'anthropic/minimax-m2.7'},
            },
            'active': 'Custom',
        }

    encrypted_payloads = []

    def fake_encrypt(value):
        encrypted_payloads.append(value)
        return 'reencrypted'

    monkeypatch.setattr(migration_143, '_decrypt_json', fake_decrypt)
    monkeypatch.setattr(migration_143, '_encrypt_json', fake_encrypt)

    updated = migration_143._upgrade_encrypted_llm_profile_column(
        Bind(),
        'user',
        'id',
        'llm_profiles',
    )

    assert updated == 1
    assert len(executed_updates) == 1
    sql = ' '.join(str(executed_updates[0][0]).split())
    assert sql.startswith('UPDATE "user" SET llm_profiles = :encrypted_profiles')
    assert executed_updates[0][1] == {
        'encrypted_profiles': 'reencrypted',
        'row_id': 'user-1',
    }
    assert encrypted_payloads == [
        {
            'profiles': {
                'Default': {'model': 'openhands/glm-5.2'},
            },
            'active': 'Default',
        }
    ]


def test_upgrade_rejects_non_postgresql(monkeypatch):
    bind = SimpleNamespace(dialect=SimpleNamespace(name='sqlite'))
    monkeypatch.setattr(
        migration_143,
        'op',
        SimpleNamespace(get_bind=lambda: bind),
    )

    with pytest.raises(RuntimeError, match='Unsupported database dialect: sqlite'):
        migration_143.upgrade()
