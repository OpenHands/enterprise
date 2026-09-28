import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '172_unset_empty_agent_profile_tools.py'
)
spec = spec_from_file_location('migration_172', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_172 = module_from_spec(spec)
spec.loader.exec_module(migration_172)


def _profiles(**profiles):
    return {'profiles': profiles, 'active': None}


def _run_upgrade(monkeypatch, rows):
    from storage import encrypt_utils

    stored = {
        org_id: encrypt_utils.encrypt_value(json.dumps(value))
        for org_id, value in rows.items()
    }
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

    monkeypatch.setattr(migration_172, 'op', SimpleNamespace(get_bind=Bind))
    migration_172.upgrade()
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
