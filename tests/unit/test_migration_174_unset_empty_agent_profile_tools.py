import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '174_unset_empty_agent_profile_tools.py'
)
spec = spec_from_file_location('migration_174', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_174 = module_from_spec(spec)
spec.loader.exec_module(migration_174)


def _profiles(**profiles):
    return {'profiles': profiles, 'active': None}


def _run_upgrade(monkeypatch, rows, raw_rows=None):
    from storage import encrypt_utils

    stored = {
        org_id: encrypt_utils.encrypt_value(json.dumps(value))
        for org_id, value in rows.items()
    }
    stored.update(raw_rows or {})
    updates = {}

    class Result:
        def mappings(self):
            return [{'id': k, 'agent_profiles': v} for k, v in stored.items()]

    class Bind:
        def execute(self, statement, params=None):
            if str(statement).lstrip().startswith('SELECT '):
                return Result()
            updates[params['id']] = json.loads(
                encrypt_utils.decrypt_value(params['agent_profiles'])
            )

    monkeypatch.setattr(migration_174, 'op', SimpleNamespace(get_bind=Bind))
    migration_174.upgrade()
    return updates


def test_upgrade_unsets_only_empty_openhands_tools(monkeypatch):
    updates = _run_upgrade(
        monkeypatch,
        {
            'org-a': _profiles(
                bare={'name': 'bare', 'tools': []},
                picked={'name': 'picked', 'tools': [{'name': 'terminal'}]},
                acp={'name': 'acp', 'agent_kind': 'acp', 'tools': []},
            ),
            'org-b': _profiles(unset={'name': 'unset', 'tools': None}),
        },
    )

    assert updates == {
        'org-a': _profiles(
            bare={'name': 'bare', 'tools': None},
            picked={'name': 'picked', 'tools': [{'name': 'terminal'}]},
            acp={'name': 'acp', 'agent_kind': 'acp', 'tools': []},
        )
    }


def test_upgrade_keeps_an_empty_tools_list_saved_at_schema_3(monkeypatch):
    updates = _run_upgrade(
        monkeypatch,
        {'org-a': _profiles(bare={'name': 'bare', 'tools': [], 'schema_version': 3})},
    )

    assert updates == {}


def test_upgrade_treats_a_non_integer_schema_version_as_legacy(monkeypatch):
    updates = _run_upgrade(
        monkeypatch,
        {
            'org-a': _profiles(
                null={'name': 'null', 'tools': [], 'schema_version': None},
                text={'name': 'text', 'tools': [], 'schema_version': '2'},
            )
        },
    )

    assert updates == {
        'org-a': _profiles(
            null={'name': 'null', 'tools': None, 'schema_version': None},
            text={'name': 'text', 'tools': None, 'schema_version': '2'},
        )
    }


def test_upgrade_skips_an_unreadable_row_and_migrates_the_rest(monkeypatch):
    updates = _run_upgrade(
        monkeypatch,
        {'org-good': _profiles(bare={'name': 'bare', 'tools': []})},
        raw_rows={'org-bad': 'not-a-ciphertext'},
    )

    assert updates == {'org-good': _profiles(bare={'name': 'bare', 'tools': None})}
