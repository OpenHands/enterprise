import json
from unittest.mock import patch

import httpx
import pytest

from storage.lite_llm_manager import LiteLlmManager


@pytest.fixture
def team():
    return {
        'team_info': {
            'team_id': 'team',
            'max_budget': 1000.0,
            'spend': 100.0,
            'budget_duration': None,
            'budget_reset_at': None,
            'members_with_roles': [{'user_id': 'user', 'role': 'user'}],
        },
        'team_memberships': [],
        'keys': [{'user_id': 'user', 'spend': 40.0}],
    }


async def observe(team, budgets=None, *, include_control_policy=False):
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == '/team/info':
            return httpx.Response(200, json=team)
        assert request.url.path == '/budget/info'
        return httpx.Response(200, json=budgets)

    with (
        patch('storage.lite_llm_manager.LITE_LLM_API_URL', 'http://litellm.test'),
        patch('storage.lite_llm_manager.LITE_LLM_API_KEY', 'test-only'),
    ):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result = await LiteLlmManager._get_team_members_financial_data(
                client, 'team', include_control_policy=include_control_policy
            )
    return result, requests


@pytest.mark.asyncio
async def test_control_observation_redacts_key_tokens_and_preserves_restrictions(team):
    team['keys'][0].update(token='test-key-token-not-real', max_budget=7, blocked=True)
    team['team_info'].update(models=['restricted-model'], blocked=True)
    result, requests = await observe(team, include_control_policy=True)
    policy = result['control_policy']
    assert policy['team']['models'] == ['restricted-model']
    assert policy['team']['blocked'] is True
    assert policy['keys'][0]['max_budget'] == 7
    assert policy['keys'][0]['blocked'] is True
    assert len(policy['keys'][0]['identity']) == 64
    assert 'test-key-token-not-real' not in json.dumps(result)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_control_observation_reads_default_even_with_private_memberships(team):
    team['keys'][0]['token'] = 'test-key-token-not-real'
    team['team_info']['metadata'] = {'team_member_budget_id': 'default'}
    team['team_memberships'] = [
        {
            'user_id': 'user',
            'spend': 12,
            'budget_id': 'private',
            'litellm_budget_table': {
                'max_budget': 50,
                'budget_duration': None,
                'budget_reset_at': None,
            },
        }
    ]
    result, requests = await observe(
        team,
        [
            {
                'budget_id': 'default',
                'max_budget': 25,
                'budget_duration': '1d',
                'budget_reset_at': '2026-09-15T00:00:00Z',
            }
        ],
        include_control_policy=True,
    )
    assert len(requests) == 2
    assert result['control_policy']['default_member']['max_budget'] == 25
    assert result['default_member_budget']['budget_duration'] == '1d'


@pytest.mark.asyncio
async def test_key_usage_is_not_a_new_membership_counter(team):
    result, requests = await observe(team)

    assert len(requests) == 1
    assert result['members']['user']['spend'] == 40.0
    assert result['member_counters']['user']['source'] == 'new_membership'
    assert result['member_counters']['user']['spend'] == 0.0
    assert result['team_reset_known'] is True


@pytest.mark.asyncio
async def test_membership_counter_and_reset_identity_are_preserved(team):
    team['team_memberships'] = [
        {
            'user_id': 'user',
            'spend': 12.0,
            'budget_id': 'private',
            'litellm_budget_table': {
                'max_budget': 50.0,
                'budget_duration': '30d',
                'budget_reset_at': '2026-10-01T00:00:00Z',
            },
        }
    ]

    result, _ = await observe(team)

    assert result['member_counters']['user'] == {
        'source': 'membership',
        'spend': 12.0,
        'budget_id': 'private',
        'effective_budget_id': 'private',
        'budget_source': 'private_member',
        'budget_duration': '30d',
        'budget_reset_at': '2026-10-01T00:00:00Z',
        'reset_known': True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('cap', [None, 0.0, 25.0])
async def test_default_member_cap_is_not_the_team_total_cap(team, cap):
    team['team_info']['metadata'] = {'team_member_budget_id': 'default'}

    result, requests = await observe(
        team, [{'budget_id': 'default', 'max_budget': cap}]
    )

    assert requests[1].method == 'POST'
    assert json.loads(requests[1].content) == {'budgets': ['default']}
    assert result['members']['user']['max_budget'] == (cap or 1000.0)
    assert result['members']['user']['uses_shared_budget'] is (not bool(cap))
    assert result['member_counters']['user']['spend'] == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'budgets',
    [[], {}, [{'budget_id': 'other', 'max_budget': 25.0}], [{'budget_id': 'default'}]],
)
async def test_unreadable_default_is_not_reported_as_unlimited(team, budgets):
    team['team_info']['metadata'] = {'team_member_budget_id': 'default'}

    with pytest.raises(ValueError, match='default member budget'):
        await observe(team, budgets)


@pytest.mark.asyncio
@pytest.mark.parametrize('cap', [-1, True, '25'])
async def test_invalid_default_cap_is_rejected(team, cap):
    team['team_info']['metadata'] = {'team_member_budget_id': 'default'}

    with pytest.raises(ValueError, match='finite non-negative'):
        await observe(team, [{'budget_id': 'default', 'max_budget': cap}])


@pytest.mark.asyncio
async def test_missing_reset_fields_remain_unknown(team):
    del team['team_info']['budget_reset_at']
    team['team_memberships'] = [
        {
            'user_id': 'user',
            'spend': 12.0,
            'budget_id': 'private',
            'litellm_budget_table': {'max_budget': 50.0},
        }
    ]

    result, _ = await observe(team)

    assert result['team_reset_known'] is False
    assert result['member_counters']['user']['reset_known'] is False


@pytest.mark.asyncio
async def test_linked_zero_cap_remains_an_explicit_member_denial(team):
    team['team_info']['metadata'] = {'team_member_budget_id': 'default'}
    team['team_memberships'] = [
        {
            'user_id': 'user',
            'spend': 12.0,
            'budget_id': 'default',
            'litellm_budget_table': {'max_budget': 0.0},
        }
    ]

    result, _ = await observe(team)

    assert result['members']['user']['max_budget'] == 0.0
    assert result['members']['user']['uses_shared_budget'] is False
