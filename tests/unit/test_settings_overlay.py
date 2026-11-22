from storage.settings_overlay import edit_scope, overlay


def test_edit_only_changes_local_document():
    org = {'limits': {'steps': 80}, 'features': ['a']}
    team = {'features': ['b']}
    person = edit_scope({}, {'limits': {'steps': 80, 'other': None}})
    assert person == {'limits': {'steps': 80}}
    org['limits']['steps'] = 120
    assert overlay(overlay(org, team), person) == {
        'limits': {'steps': 80},
        'features': ['b'],
    }
    reset = edit_scope(person, {'limits': {'steps': None}})
    assert reset == {}
    assert person == {'limits': {'steps': 80}}
    assert overlay(overlay(org, team), reset)['limits']['steps'] == 120
