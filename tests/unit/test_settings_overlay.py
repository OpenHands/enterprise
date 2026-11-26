from openhands.app_server.settings.scoped import (
    Inherit,
    Set,
    edit,
    resolve,
)


def test_pin_and_inherit_follow_changes_to_the_parent():
    org = {'limits': {'steps': 80}, 'features': ['a']}
    team = {'features': ['b']}
    person = edit({}, [Set(('limits', 'steps'), 80)])
    assert person == {'limits': {'steps': 80}}
    org['limits']['steps'] = 120
    assert resolve(org, team, person) == {'limits': {'steps': 80}, 'features': ['b']}
    reset = edit(person, [Inherit(('limits', 'steps'))])
    assert reset == {}
    assert person == {'limits': {'steps': 80}}
    assert resolve(org, team, reset)['limits']['steps'] == 120


def test_null_is_a_value_not_a_reset():
    org = {'limit': 3}
    team = edit({}, [Set(('limit',), None)])
    assert resolve(org, team) == {'limit': None}
    assert resolve(org, edit(team, [Inherit(('limit',))])) == org


def test_sibling_reset_is_a_noop_and_reads_are_detached():
    original = {'model': {'tools': ['a']}}
    derived = edit(original, [Inherit(('model', 'absent', 'nested'))])
    view = resolve(original, derived)
    view['model']['tools'].clear()
    assert derived == original == {'model': {'tools': ['a']}}
