"""Adoption arithmetic and preview staleness must not depend on request retries."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from server.services.budget_adoption_plan import (
    BudgetAdoptionRequest,
    BudgetAdoptionUnsupported,
    adoption_fingerprint,
    build_adoption_plan,
)
from storage.budget_control import BudgetControlConflict

ORG = uuid4()
NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)


def observation():
    return {
        'team_spend': 302.55,
        'team_max_budget': 1000,
        'team_budget_duration': None,
        'team_budget_reset_at': None,
        'team_reset_known': True,
        'default_member_budget_id': None,
        'default_member_budget': None,
        'members': {'u1': {'spend': 12}, 'u2': {'spend': 40}},
        'member_counters': {
            'u1': {
                'source': 'membership',
                'spend': 12,
                'budget_id': 'b1',
                'effective_budget_id': 'b1',
                'budget_source': 'private_member',
                'budget_duration': None,
                'budget_reset_at': None,
                'reset_known': True,
            },
            'u2': {
                'source': 'new_membership',
                'spend': 0,
                'budget_id': None,
                'effective_budget_id': None,
                'budget_source': 'team',
                'budget_duration': None,
                'budget_reset_at': None,
                'reset_known': True,
            },
        },
        'control_policy': {
            'team': {
                'max_budget': 1000,
                'models': ['restricted-model'],
                'blocked': True,
            },
            'members': {'u1': {'rpm_limit': 9}},
            'keys': [{'identity': 'hashed-key-identity', 'max_budget': 7}],
            'default_member': {},
        },
    }


def request(observed, **kwargs):
    values = {
        'preview_fingerprint': adoption_fingerprint(ORG, 0, {'u1', 'u2'}, observed),
        'idempotency_key': 'confirm-1',
        'current_team_allowance': 100,
        'current_default_member_allowance': 10,
        'current_member_allowances': {},
        'future_monthly_limit': 700,
        'future_default_member_limit': 80,
        'reset_day': 15,
        'replace_native_reset_schedules': False,
    }
    values.update(kwargs)
    return BudgetAdoptionRequest(**values)


def plan(observed, confirmation):
    return build_adoption_plan(ORG, 0, {'u1', 'u2'}, observed, confirmation, now=NOW)


def test_adoption_uses_counter_being_enforced_and_separates_future_policy():
    observed = observation()
    result = plan(observed, request(observed))
    assert result['expected_team_cap'] == 402.55
    assert result['expected_member_caps'] == {'u1': 22, 'u2': 10}
    assert result['member_baselines'] == {'u1': 12, 'u2': 0}
    assert result['future_policy']['monthly_limit'] == 700
    assert result['future_policy']['default_user_monthly_limit'] == 80
    assert result['cycle_start_at'] == NOW.isoformat()
    assert result['cycle_end_at'] == datetime(2026, 9, 15, tzinfo=UTC).isoformat()


def test_ordinary_spend_growth_does_not_expire_preview_but_confirmation_samples_once():
    observed = observation()
    confirmation = request(observed)
    observed['team_spend'] += 15
    observed['member_counters']['u1']['spend'] += 3
    observed['members']['u2']['spend'] += 20
    result = plan(observed, confirmation)
    assert result['expected_team_cap'] == 417.55
    assert result['expected_member_caps'] == {'u1': 25, 'u2': 10}


@pytest.mark.parametrize(
    'change', ['cap', 'roster', 'counter', 'reset', 'key', 'blocked']
)
def test_policy_or_counter_identity_change_requires_new_preview(change):
    observed = observation()
    confirmation = request(observed)
    if change == 'cap':
        observed['control_policy']['team']['max_budget'] = 900
    elif change == 'roster':
        observed['member_counters']['u3'] = deepcopy(observed['member_counters']['u1'])
    elif change == 'counter':
        observed['member_counters']['u1']['budget_id'] = 'replacement'
    elif change == 'reset':
        observed['team_budget_reset_at'] = '2026-09-15T00:00:00Z'
    elif change == 'key':
        observed['control_policy']['keys'][0]['identity'] = 'replacement-key'
    else:
        observed['control_policy']['team']['blocked'] = False
    with pytest.raises(BudgetControlConflict, match='new preview'):
        plan(observed, confirmation)


def test_generation_change_rejects_old_preview():
    observed = observation()
    with pytest.raises(BudgetControlConflict):
        build_adoption_plan(ORG, 1, {'u1', 'u2'}, observed, request(observed), now=NOW)


@pytest.mark.parametrize('bad_counter', [None, True, -1, float('nan'), float('inf')])
def test_unknown_or_invalid_counters_cannot_be_adopted(bad_counter):
    observed = observation()
    observed['member_counters']['u1']['spend'] = bad_counter
    with pytest.raises(BudgetAdoptionUnsupported):
        request(observed)


def test_missing_membership_cannot_silently_disappear_from_takeover():
    observed = observation()
    del observed['member_counters']['u1']
    with pytest.raises(BudgetAdoptionUnsupported, match='missing from LiteLLM'):
        request(observed)


def test_unknown_reset_is_different_from_explicitly_disabled_reset():
    observed = observation()
    observed['team_reset_known'] = False
    with pytest.raises(BudgetAdoptionUnsupported, match='reset schedule'):
        request(observed)


def test_reset_normalization_requires_explicit_consent():
    observed = observation()
    observed['member_counters']['u1']['budget_duration'] = '1d'
    with pytest.raises(BudgetAdoptionUnsupported, match='Confirm replacing'):
        plan(observed, request(observed))
    result = plan(observed, request(observed, replace_native_reset_schedules=True))
    assert all(write['body']['budget_duration'] is None for write in result['writes'])


def test_targets_preserve_operator_blocks_models_keys_and_rate_limits():
    observed = observation()
    before = deepcopy(observed)
    result = plan(observed, request(observed))
    assert result['preserved_policy'] == observed['control_policy']
    assert observed == before
    for write in result['writes']:
        assert (
            not {'models', 'blocked', 'rpm_limit', 'tpm_limit', 'metadata', 'spend'}
            & write['body'].keys()
        )
        assert write['path'] in {'/team/update', '/team/member_update'}


def test_explicit_zero_member_allowance_is_a_zero_private_cap_not_unlimited():
    observed = observation()
    result = plan(observed, request(observed, current_member_allowances={'u2': 0}))
    assert result['expected_member_caps']['u2'] == 0


def test_member_cap_can_be_removed_without_removing_other_restrictions():
    observed = observation()
    result = plan(observed, request(observed, current_member_allowances={'u1': None}))
    assert result['expected_member_caps']['u1'] is None
    assert result['preserved_policy']['members']['u1']['rpm_limit'] == 9


@pytest.mark.parametrize(
    'field',
    [
        'current_team_allowance',
        'current_default_member_allowance',
        'future_monthly_limit',
        'future_default_member_limit',
    ],
)
@pytest.mark.parametrize('value', [True, -1, float('nan'), float('inf')])
def test_request_does_not_coerce_invalid_allowances(field, value):
    observed = observation()
    with pytest.raises(ValidationError):
        request(observed, **{field: value})


def test_partial_takeover_tightens_team_first_and_raises_team_last():
    observed = observation()
    assert plan(observed, request(observed))['writes'][0]['path'] == '/team/update'
    observed['team_max_budget'] = 100
    observed['control_policy']['team']['max_budget'] = 100
    assert plan(observed, request(observed))['writes'][-1]['path'] == '/team/update'


def test_default_member_policy_must_be_observed_even_when_every_member_has_private_cap():
    observed = observation()
    observed['default_member_budget_id'] = 'unobserved-default'
    with pytest.raises(BudgetAdoptionUnsupported, match='default member budget'):
        request(observed)
