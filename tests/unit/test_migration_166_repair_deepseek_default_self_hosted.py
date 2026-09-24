import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '166_repair_deepseek_default_self_hosted.py'
)
spec = spec_from_file_location('migration_166', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_166 = module_from_spec(spec)
spec.loader.exec_module(migration_166)

DEFAULT_MODEL = migration_166._MANAGED_DEFAULT  # 'openhands/deepseek-v4-flash'


def _encrypt(payload: dict) -> str:
    from storage.encrypt_utils import encrypt_value

    return encrypt_value(json.dumps(payload))


def _decrypt(raw: str) -> dict:
    from storage.encrypt_utils import decrypt_value

    return json.loads(decrypt_value(raw))


def _profiles(default_model=None, active='Default', extra=None):
    profiles = {}
    if default_model is not None:
        profiles['Default'] = {
            'model': default_model,
            'base_url': None,
            'api_key': None,
        }
    if extra:
        profiles.update(extra)
    return {'profiles': profiles, 'active': active}


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return list(self._rows)


class _Bind:
    """Fake alembic bind: serves a fixture org/verified_models SELECT and
    captures every write (DELETE / UPDATE) for assertion."""

    dialect = SimpleNamespace(name='postgresql')

    def __init__(self, org_rows, verified_rows):
        self._org_rows = org_rows
        self._verified_rows = verified_rows
        self.writes = []  # (kind, table, where/values)

    def execute(self, statement, params=None):
        sql = ' '.join(str(statement).split())
        if sql.startswith('SELECT org.id'):
            return _Result(self._org_rows)
        if sql.startswith('SELECT'):
            return _Result(self._verified_rows)
        # Core statements carry their bound params internally; extract them so
        # the written llm_profiles ciphertext is inspectable.
        compiled_params = {}
        try:
            compiled_params = dict(statement.compile().params)
        except Exception:
            pass
        self.writes.append((sql, compiled_params))
        return None


def _run(monkeypatch, org_rows, verified_rows=(), web_host='openhands.example.com'):
    bind = _Bind(org_rows, verified_rows)
    monkeypatch.setenv('WEB_HOST', web_host)
    monkeypatch.setattr(
        migration_166, 'op', SimpleNamespace(get_bind=lambda: bind)
    )
    migration_166.upgrade()
    return bind


def _org_writes(bind):
    return [w for w in bind.writes if w[0].startswith('UPDATE org')]


def _delete_writes(bind):
    return [w for w in bind.writes if 'DELETE FROM verified_models' in w[0]]


# ── classify unit ───────────────────────────────────────────────────────────


def test_classify_noop_when_no_default():
    assert migration_166._classify({'profiles': {}}, None) == 'noop'


def test_classify_noop_when_concrete_default():
    assert (
        migration_166._classify(_profiles('anthropic/claude'), 'anthropic/claude')
        == 'noop'
    )


def test_classify_restore_when_bogus_default_and_byok_legacy():
    assert (
        migration_166._classify(_profiles(DEFAULT_MODEL), 'anthropic/claude')
        == 'restore'
    )


def test_classify_strip_when_bogus_default_and_no_legacy():
    assert migration_166._classify(_profiles(DEFAULT_MODEL), None) == 'strip'


def test_classify_review_when_bogus_default_and_managed_legacy():
    assert (
        migration_166._classify(_profiles(DEFAULT_MODEL), DEFAULT_MODEL) == 'review'
    )


# ── WEB_HOST gate ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    'web_host',
    ['app.all-hands.dev', 'staging.all-hands.dev', 'dev.all-hands.dev'],
)
def test_upgrade_noop_on_saas(monkeypatch, web_host):
    bind = _run(monkeypatch, [], web_host=web_host)
    assert bind.writes == []


@pytest.mark.parametrize('web_host', ['', 'openhands.example.com'])
def test_upgrade_runs_on_self_hosted(monkeypatch, web_host):
    bind = _run(monkeypatch, [], web_host=web_host)
    # Even with no org rows, the verified_models DELETE still runs.
    assert _delete_writes(bind)


# ── step 1: verified_models delete ───────────────────────────────────────────


def test_upgrade_deletes_deepseek_verified_models_row(monkeypatch):
    bind = _run(monkeypatch, [])
    deletes = _delete_writes(bind)
    assert len(deletes) == 1
    sql, params = deletes[0]
    assert 'DELETE FROM verified_models' in sql
    assert params['provider_1'] == 'openhands'
    assert params['model_name_1'] == 'deepseek-v4-flash'


# ── step 2: per-org restore ──────────────────────────────────────────────────


def test_upgrade_restores_c2_default_from_legacy_llm(monkeypatch):
    org_id = uuid4()
    legacy = {
        'model': 'anthropic/claude-sonnet',
        'base_url': 'https://api.anthropic.com',
        'api_key': 'sk-byok',
    }
    org_rows = [
        {
            'id': org_id,
            'agent_settings': {'llm': legacy},
            'llm_profiles': _encrypt(_profiles(DEFAULT_MODEL)),
        }
    ]
    bind = _run(monkeypatch, org_rows)
    writes = _org_writes(bind)
    assert len(writes) == 1
    # Re-encrypt and inspect the restored Default.
    payload = _decrypt(writes[0][1]['llm_profiles'])
    restored = payload['profiles']['Default']
    assert restored['model'] == 'anthropic/claude-sonnet'
    assert restored['api_key'] == 'sk-byok'
    assert payload['active'] == 'Default'


def test_upgrade_strips_c3_phantom_when_no_legacy(monkeypatch):
    org_id = uuid4()
    org_rows = [
        {
            'id': org_id,
            'agent_settings': {},
            'llm_profiles': _encrypt(_profiles(DEFAULT_MODEL)),
        }
    ]
    bind = _run(monkeypatch, org_rows)
    writes = _org_writes(bind)
    assert len(writes) == 1
    payload = _decrypt(writes[0][1]['llm_profiles'])
    assert 'Default' not in payload['profiles']
    assert payload['active'] is None


def test_upgrade_leaves_c1_review_unwritten(monkeypatch):
    org_id = uuid4()
    org_rows = [
        {
            'id': org_id,
            'agent_settings': {'llm': {'model': DEFAULT_MODEL}},
            'llm_profiles': _encrypt(_profiles(DEFAULT_MODEL)),
        }
    ]
    bind = _run(monkeypatch, org_rows)
    # No org UPDATE — only the verified_models DELETE.
    assert _org_writes(bind) == []


def test_upgrade_skips_noop_orgs(monkeypatch):
    # Concrete BYOK Default, never corrupted: stored bytes left untouched.
    org_rows = [
        {
            'id': uuid4(),
            'agent_settings': {'llm': {'model': 'anthropic/claude'}},
            'llm_profiles': _encrypt(_profiles('anthropic/claude')),
        }
    ]
    bind = _run(monkeypatch, org_rows)
    assert _org_writes(bind) == []


def test_upgrade_skips_org_with_no_profiles(monkeypatch):
    org_rows = [
        {'id': uuid4(), 'agent_settings': {}, 'llm_profiles': None}
    ]
    bind = _run(monkeypatch, org_rows)
    assert _org_writes(bind) == []


def test_upgrade_preserves_other_profiles_on_restore(monkeypatch):
    org_id = uuid4()
    org_rows = [
        {
            'id': org_id,
            'agent_settings': {'llm': {'model': 'anthropic/claude'}},
            'llm_profiles': _encrypt(
                _profiles(DEFAULT_MODEL, extra={'Team': {'model': 'gpt-4o'}})
            ),
        }
    ]
    bind = _run(monkeypatch, org_rows)
    payload = _decrypt(_org_writes(bind)[0][1]['llm_profiles'])
    assert payload['profiles']['Team'] == {'model': 'gpt-4o'}
    assert payload['profiles']['Default']['model'] == 'anthropic/claude'


# ── idempotency ──────────────────────────────────────────────────────────────


def test_upgrade_idempotent_second_run_writes_no_org_updates(monkeypatch):
    # First run restores a C2 org to a concrete Default.
    org_id = uuid4()
    org_rows = [
        {
            'id': org_id,
            'agent_settings': {'llm': {'model': 'anthropic/claude'}},
            'llm_profiles': _encrypt(_profiles(DEFAULT_MODEL)),
        }
    ]
    bind = _run(monkeypatch, org_rows)
    assert _org_writes(bind)

    # Second run: Default is now concrete (noop) — no org UPDATE.
    restored_payload = _decrypt(_org_writes(bind)[0][1]['llm_profiles'])
    org_rows[0]['llm_profiles'] = _encrypt(restored_payload)
    bind2 = _run(monkeypatch, org_rows)
    assert _org_writes(bind2) == []
