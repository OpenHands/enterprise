from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '156_backfill_openhands_free_default_model.py'
)
spec = spec_from_file_location('migration_156', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_156 = module_from_spec(spec)
spec.loader.exec_module(migration_156)


def test_replace_model_values_rewrites_previous_platform_default_to_public_model():
    replacements = {
        'openhands/deepseek-v4-flash': 'openhands/gpt-5.2',
        'litellm_proxy/deepseek-v4-flash': 'openhands/gpt-5.2',
    }
    payload = {
        'llm': {'model': 'openhands/deepseek-v4-flash'},
        'profiles': {
            'Default': {'model': 'litellm_proxy/deepseek-v4-flash'},
            'Pinned': {'model': 'anthropic/deepseek-v4-flash'},
            'Bare': {'model': 'deepseek-v4-flash'},
        },
    }

    replaced, changed = migration_156._replace_model_values(payload, replacements)

    assert changed is True
    assert replaced['llm']['model'] == 'openhands/gpt-5.2'
    assert replaced['profiles']['Default']['model'] == 'openhands/gpt-5.2'
    assert replaced['profiles']['Pinned']['model'] == 'anthropic/deepseek-v4-flash'
    assert replaced['profiles']['Bare']['model'] == 'deepseek-v4-flash'
    assert payload['llm']['model'] == 'openhands/deepseek-v4-flash'


def test_rewrite_previous_platform_default_noops_when_db_default_is_unchanged(
    monkeypatch,
):
    calls = []
    monkeypatch.setenv('WEB_HOST', 'pr-796.staging.all-hands.dev')

    class Result:
        def scalar(self):
            return 'deepseek-v4-flash'

    class Bind:
        def execute(self, statement):
            calls.append(str(statement))
            return Result()

    monkeypatch.setattr(
        migration_156,
        '_update_org_member_agent_settings_diff',
        lambda _replacements: calls.append('org_member'),
    )
    monkeypatch.setattr(
        migration_156,
        '_update_json_column',
        lambda *_args: calls.append('json'),
    )
    monkeypatch.setattr(
        migration_156,
        '_update_encrypted_json_column',
        lambda *_args: calls.append('encrypted'),
    )

    migration_156._rewrite_previous_platform_default_references(Bind())

    assert len(calls) == 1
    assert 'SELECT model_name' in calls[0]


def test_rewrite_previous_platform_default_skips_non_saas_hosts(monkeypatch):
    calls = []
    monkeypatch.setenv('WEB_HOST', 'self-hosted.example.com')
    monkeypatch.setattr(
        migration_156,
        '_current_openhands_default_model_name',
        lambda _bind: calls.append('selected') or 'gpt-5.2',
    )

    migration_156._rewrite_previous_platform_default_references(object())

    assert calls == []
