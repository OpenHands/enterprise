import copy
from uuid import uuid4

import pytest
from sqlalchemy import text

from scripts.audit_agent_profiles import audit_envelope
from storage.org import Org
from tests.postgres_testdb import run_alembic


def _profile(**updates):
    return {
        'id': str(uuid4()),
        'name': 'qa-profile',
        'agent_kind': 'openhands',
        'llm_profile_ref': 'Default',
        'tools': [],
        **updates,
    }


@pytest.mark.parametrize('version', [None, True, '2', 0, -1])
def test_audit_reports_invalid_versions_without_mutating_input(version):
    profile = _profile(schema_version=version)
    envelope = {'profiles': {profile['id']: profile}, 'active': profile['id']}
    original = copy.deepcopy(envelope)
    findings = audit_envelope('org', envelope)
    assert findings == [
        {'org_id': 'org', 'profile_id': profile['id'], 'kind': 'unreadable_profile'}
    ]
    assert envelope == original


@pytest.mark.parametrize('starting_revision', ['192', '195', '198', '199'])
def test_real_upgrade_preserves_opaque_and_intentionally_bare_profiles(
    starting_revision,
    test_database,
    session_maker,
):
    run_alembic(
        test_database.server, test_database.name, 'downgrade', starting_revision
    )
    legacy = _profile(name='legacy', schema_version=2)
    bare = _profile(name='bare', schema_version=3)
    future = _profile(name='future', schema_version=99, future_settings={'keep': True})
    invalid = _profile(name='invalid', schema_version=None)
    envelope = {
        'profiles': {p['id']: p for p in (legacy, bare, future, invalid)},
        'active': future['id'],
    }
    original = copy.deepcopy(envelope)
    org_id = uuid4()
    with session_maker() as session:
        session.add(Org(id=org_id, name='upgrade-qa', agent_profiles=envelope))
        session.commit()
    run_alembic(test_database.server, test_database.name, 'upgrade', 'head')
    with session_maker() as session:
        org = session.get(Org, org_id)
        migrated = org.agent_profiles
        assert (
            session.execute(
                text('SELECT version_num FROM alembic_version')
            ).scalar_one()
            == '200'
        )
    expected = copy.deepcopy(original)
    expected['profiles'][legacy['id']]['tools'] = None
    assert migrated == expected
    assert {f['kind'] for f in audit_envelope(str(org_id), migrated)} == {
        'future_profile',
        'unreadable_profile',
    }
