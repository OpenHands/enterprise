"""Two actual proxy processes must observe each other's budget changes."""

import asyncio
import os
from contextlib import closing
from time import monotonic
from uuid import uuid4

import docker
import httpx
import pytest

MASTER = 'sk-budget-control-local-only'


@pytest.fixture
async def proxies():
    with closing(docker.from_env()) as docker_client:
        for service, port in [('proxy', '41500'), ('peer', '41501')]:
            containers = docker_client.containers.list(
                filters={
                    'label': [
                        'com.docker.compose.project=budget-control-cache',
                        f'com.docker.compose.service={service}',
                    ]
                }
            )
            assert len(containers) == 1, 'Start the isolated multi-proxy test stack'
            assert containers[0].attrs['NetworkSettings']['Ports']['4000/tcp'] == [
                {'HostIp': '127.0.0.1', 'HostPort': port}
            ]
        redis_containers = docker_client.containers.list(
            filters={
                'label': [
                    'com.docker.compose.project=budget-control-cache',
                    'com.docker.compose.service=redis',
                ]
            }
        )
        assert len(redis_containers) == 1, 'Expected the isolated coordination Redis'
        subscription_check = redis_containers[0].exec_run(
            [
                'redis-cli',
                '--raw',
                'PUBSUB',
                'NUMSUB',
                'litellm_proxy.auth_cache_invalidation',
            ]
        )
        assert subscription_check.exit_code == 0
        assert subscription_check.output.splitlines() == [
            b'litellm_proxy.auth_cache_invalidation',
            b'2',
        ], 'Both proxy cache-invalidation subscribers must be ready before testing'
    async with (
        httpx.AsyncClient(
            base_url='http://127.0.0.1:41500',
            headers={'Authorization': f'Bearer {MASTER}'},
            timeout=30,
        ) as primary,
        httpx.AsyncClient(
            base_url='http://127.0.0.1:41501',
            headers={'Authorization': f'Bearer {MASTER}'},
            timeout=30,
        ) as peer,
    ):
        for proxy in (primary, peer):
            (await proxy.get('/health/readiness')).raise_for_status()
        yield primary, peer


async def post(proxy, endpoint, body):
    response = await proxy.post(endpoint, json=body)
    response.raise_for_status()
    return response.json()


async def infer(proxy, key):
    return await proxy.post(
        '/chat/completions',
        headers={'Authorization': f'Bearer {key}'},
        json={
            'model': 'budget-test',
            'messages': [{'role': 'user', 'content': 'Count this cache test.'}],
            'max_tokens': 8,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'restriction',
    ['member', 'team', 'team_block', 'dedicated_block', 'dedicated_unblock'],
)
async def test_warm_peer_observes_admission_change_with_same_key(proxies, restriction):
    primary, peer = proxies
    user_id, team_id = str(uuid4()), str(uuid4())
    created_user = created_team = False
    try:
        await post(
            primary,
            '/user/new',
            {'user_id': user_id, 'auto_create_key': False, 'send_invite_email': False},
        )
        created_user = True
        await post(
            primary,
            '/team/new',
            {'team_id': team_id, 'max_budget': 1000, 'models': ['budget-test']},
        )
        created_team = True
        await post(
            primary,
            '/team/member_add',
            {
                'team_id': team_id,
                'member': {'user_id': user_id, 'role': 'user'},
                'max_budget_in_team': 10000,
            },
        )
        key = (
            await post(
                primary, '/key/generate', {'team_id': team_id, 'user_id': user_id}
            )
        )['key']
        (await infer(primary, key)).raise_for_status()
        for _ in range(45):
            response = await primary.get('/team/info', params={'team_id': team_id})
            response.raise_for_status()
            membership = next(
                member
                for member in response.json()['team_memberships']
                if member['user_id'] == user_id
            )
            spend = membership['spend']
            if spend > 0:
                break
            await asyncio.sleep(1)
        else:
            pytest.fail('Expected actual nonzero membership spend')

        endpoint = '/team/member_update' if restriction == 'member' else '/team/update'
        block_after = restriction in {'team_block', 'dedicated_block'}
        dedicated = restriction.startswith('dedicated_')
        body = (
            {'team_id': team_id, 'user_id': user_id, 'max_budget_in_team': spend}
            if restriction == 'member'
            else {'team_id': team_id, 'blocked': not block_after}
        )
        if dedicated:
            endpoint = '/team/unblock' if block_after else '/team/block'
            body = {'team_id': team_id}
        await post(primary, endpoint, body)
        for proxy in (primary, peer):
            denied = await infer(proxy, key)
            assert denied.status_code == (
                200 if block_after else 429 if restriction == 'member' else 401
            ), denied.text
            if not block_after:
                assert (
                    'budget' if restriction == 'member' else 'blocked'
                ) in denied.text.lower()

        warmed_at = monotonic()
        body = (
            {'team_id': team_id, 'user_id': user_id, 'max_budget_in_team': spend + 20}
            if restriction == 'member'
            else {'team_id': team_id, 'blocked': block_after}
        )
        if dedicated:
            endpoint = '/team/block' if block_after else '/team/unblock'
            body = {'team_id': team_id}
        await post(primary, endpoint, body)
        expected_status = 401 if block_after else 200
        observe_seconds = float(os.getenv('BUDGET_TEST_CACHE_OBSERVE_SECONDS', '1'))
        assert 0 <= observe_seconds <= 90
        for proxy in (peer, primary):
            reopened = await infer(proxy, key)
            recovery = []
            if reopened.status_code != expected_status:
                deadline = monotonic() + observe_seconds
                while monotonic() < deadline:
                    await asyncio.sleep(0.1 if observe_seconds <= 2 else 1)
                    subsequent = await infer(proxy, key)
                    recovery.append(
                        (round(monotonic() - warmed_at, 3), subsequent.status_code)
                    )
                    if subsequent.status_code == expected_status:
                        break
            assert reopened.status_code == expected_status, (
                f'{reopened.text}; subsequent={recovery}'
            )
        # Cache expiry must not masquerade as propagated invalidation.
        assert monotonic() - warmed_at < 4
    finally:
        if created_team:
            await post(primary, '/team/delete', {'team_ids': [team_id]})
        if created_user:
            await post(primary, '/user/delete', {'user_ids': [user_id]})
