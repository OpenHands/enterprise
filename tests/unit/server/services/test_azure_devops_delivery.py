import base64
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from openhands.app_server.integrations.azure_devops.service.base import (
    AzureDevOpsApiError,
)
from openhands.app_server.integrations.azure_devops.service.webhooks import (
    AzureDevOpsWebhooksMixin,
)
from server.routes.integration import azure_devops as routes
from server.services import azure_devops_delivery as delivery
from storage import azure_devops_webhook_diagnostics as diagnostics
from storage import azure_devops_webhook_store as installs


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.test_lock = MagicMock()
        self.test_lock.acquire = AsyncMock(return_value=True)
        self.test_lock.release = AsyncMock()

    def lock(self, *args, **kwargs):
        return self.test_lock

    async def set(self, key, value, *, xx=False, **kwargs):
        if xx and key not in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def setup(monkeypatch, async_session_maker):
    monkeypatch.setattr(diagnostics, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(installs, 'a_session_maker', async_session_maker)
    redis = FakeRedis()
    monkeypatch.setattr(delivery, 'get_redis_client_async', lambda: redis)
    monkeypatch.setattr(routes, 'get_redis_client_async', lambda: redis)
    monkeypatch.setattr(routes, 'IS_LOCAL_DEPLOYMENT', False)
    monkeypatch.setattr(routes, 'AZURE_DEVOPS_WEBHOOK_SECRET', 'secret')
    monkeypatch.setattr(routes, 'HOST_URL', 'https://app.example.com')
    monkeypatch.setattr(routes, 'AUTOMATION_EVENT_FORWARDING_ENABLED', False)
    monkeypatch.setattr(delivery, 'DELIVERY_TIMEOUT', 0.02)
    return redis


def hooks():
    return [
        {
            'id': str(index),
            'eventType': event_type,
            'status': 'enabled',
            'publisherId': 'tfs',
            'publisherInputs': {},
            'consumerId': 'webHooks',
            'consumerActionId': 'httpRequest',
            'consumerInputs': {
                'url': routes.azure_devops_webhook_url(),
                'basicAuthUsername': 'openhands',
                'basicAuthPassword': 'secret',
            },
        }
        for index, event_type in enumerate(
            (
                routes.AZURE_DEVOPS_PR_COMMENT_EVENT,
                routes.AZURE_DEVOPS_WORK_ITEM_COMMENT_EVENT,
            )
        )
    ]


async def callback(token, secret='secret'):
    tasks = BackgroundTasks()
    encoded = base64.b64encode(f'openhands:{secret}'.encode()).decode()
    request = Request(
        {'type': 'http', 'headers': [(b'x-openhands-delivery-probe', token.encode())]}
    )
    response = await routes.azure_devops_events(
        request, tasks, None, f'Basic {encoded}'
    )
    assert not tasks.tasks
    return response


@pytest.mark.asyncio
async def test_round_trip_requires_authenticated_receipts_and_persists_only_safe_evidence(
    setup, monkeypatch
):
    monkeypatch.setattr(routes, 'AUTOMATION_EVENT_FORWARDING_ENABLED', True)
    service = MagicMock(organization='contoso')

    async def send(hook, token):
        assert (await callback(token)).status_code == 200
        return {'id': 3, 'status': 'queued', 'details': {'request': 'SECRET REQUEST'}}

    service.create_service_hook_test_notification = AsyncMock(side_effect=send)
    result = await delivery.verify_delivery(service, hooks(), 'secret', 'owner')
    assert result.outcome == 'verified'
    assert all(check.received_at for check in result.checks)
    saved = await diagnostics.get_last_delivery_test('CONTOSO')
    assert saved['outcome'] == 'verified'
    assert saved['checks'][0]['notification_id'] == 3
    assert 'SECRET' not in str(saved)
    assert 'basicAuthPassword' not in str(saved)
    assert setup.values == {}
    setup.test_lock.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_queued_notification_is_not_delivery_success(setup):
    service = MagicMock(organization='contoso')
    service.create_service_hook_test_notification = AsyncMock(
        return_value={'id': 1, 'status': 'queued'}
    )
    service.get_service_hook_test_notification = AsyncMock(
        return_value={
            'result': 'failed',
            'details': {'response': 'Status Code: 403\nSECRET RESPONSE'},
        }
    )
    result = await delivery.verify_delivery(service, hooks(), 'secret', 'owner')
    assert result.outcome == 'failed'
    assert all(check.error_code == 'callback_not_received' for check in result.checks)
    assert not any(check.received_at for check in result.checks)
    assert all(check.callback_http_status == 403 for check in result.checks)
    assert 'SECRET' not in str(await diagnostics.get_last_delivery_test('contoso'))
    assert setup.values == {}
    assert (await diagnostics.get_last_delivery_test('contoso'))['outcome'] == 'failed'


@pytest.mark.asyncio
async def test_api_rejection_does_not_leak_raw_response(setup):
    service = MagicMock(organization='contoso')
    service.create_service_hook_test_notification = AsyncMock(
        side_effect=AzureDevOpsApiError(403, 'secret: sensitive request')
    )
    result = await delivery.verify_delivery(service, hooks(), 'secret', 'owner')
    assert result.outcome == 'failed'
    assert all(check.azure_http_status == 403 for check in result.checks)
    assert 'sensitive' not in str(await diagnostics.get_last_delivery_test('contoso'))


@pytest.mark.asyncio
async def test_probe_authentication_unknown_and_expired_never_dispatch(
    setup, monkeypatch
):
    monkeypatch.setattr(routes, 'AUTOMATION_EVENT_FORWARDING_ENABLED', True)
    token = 'a' * 64
    key = 'azure_devops_probe:' + token
    await setup.set(key, 'pending')
    with pytest.raises(HTTPException) as rejected:
        await callback(token, 'wrong')
    assert rejected.value.status_code == 403
    assert await setup.get(key) == 'pending'
    await setup.delete(key)
    with pytest.raises(HTTPException) as expired:
        await callback(token)
    assert expired.value.status_code == 410
    assert not await delivery.receive_delivery_probe('../unexpected')


@pytest.mark.asyncio
async def test_concurrent_test_keeps_existing_result(setup):
    service = MagicMock(organization='contoso')
    await diagnostics.save_webhook_diagnostics(
        'contoso', last_delivery_test={'test_id': 'active'}
    )
    setup.test_lock.acquire.return_value = False
    result = await delivery.verify_delivery(service, hooks(), 'secret', 'owner')
    assert result.outcome == 'failed'
    assert 'already running' in result.error
    assert (await diagnostics.get_last_delivery_test('contoso'))['test_id'] == 'active'


@pytest.mark.asyncio
async def test_status_invalidates_delivery_when_hook_config_or_secret_changes(setup):
    selected = hooks()
    result = delivery.DeliveryTestResult(outcome='verified', finished_at=delivery.now())
    await delivery.save_delivery_result(
        'contoso',
        result,
        delivery.configuration_fingerprint(selected, 'secret'),
        'owner',
    )
    assert (
        await routes._last_delivery_test(
            'contoso', selected, routes.azure_devops_webhook_url()
        )
    ).outcome == 'verified'
    changed = deepcopy(selected)
    changed[0]['consumerInputs']['basicAuthPassword'] = 'changed'
    assert (
        await routes._last_delivery_test(
            'contoso', changed, routes.azure_devops_webhook_url()
        )
        is None
    )
    changed = deepcopy(selected)
    changed[0]['status'] = 'disabled'
    assert (
        await routes._last_delivery_test(
            'contoso', changed, routes.azure_devops_webhook_url()
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('delivered', [True, False])
async def test_install_automatically_tests_delivery_and_preserves_hooks_on_failure(
    setup, monkeypatch, delivered
):
    service = MagicMock(organization='contoso')
    service.get_accessible_organizations = AsyncMock(return_value=['contoso'])
    service.get_service_hook_permissions = AsyncMock(return_value=(True, True))
    created = hooks()
    service.list_service_hook_subscriptions = AsyncMock(side_effect=[[], created])
    service.create_pr_comment_service_hook = AsyncMock(return_value=created[0])
    service.create_work_item_comment_service_hook = AsyncMock(return_value=created[1])
    service.delete_service_hook_subscription = AsyncMock()

    async def send(hook, token):
        if delivered:
            await callback(token)
        return {'id': 1}

    service.create_service_hook_test_notification = AsyncMock(side_effect=send)
    monkeypatch.setattr(routes, 'SaaSAzureDevOpsService', lambda **kwargs: service)
    result = await routes.reinstall_azure_devops_webhook(
        user_id='owner', organization='contoso'
    )
    assert result.success
    assert result.delivery_test.outcome == ('verified' if delivered else 'failed')
    assert service.create_service_hook_test_notification.await_count == 2
    service.delete_service_hook_subscription.assert_not_awaited()
    saved = await diagnostics.get_last_delivery_test('contoso')
    assert saved['outcome'] == result.delivery_test.outcome


@pytest.mark.asyncio
async def test_test_notification_copies_installed_config_without_modifying_hooks(setup):
    service = MagicMock(base_url='https://dev.azure.com/contoso')
    service._make_request = AsyncMock(return_value=({'id': 5}, {}))
    hook = hooks()[0]
    hook['consumerInputs']['httpHeaders'] = (
        'X-OpenHands-Notification-Type:PushNotification'
    )
    original = deepcopy(hook)
    await AzureDevOpsWebhooksMixin.create_service_hook_test_notification(
        service, hook, 'token'
    )
    call = service._make_request.call_args.kwargs
    assert 'testnotifications' in call['url']
    assert 'useRealData=false' in call['url']
    assert call['params']['subscriptionId'] == hook['id']
    details = call['params']['details']
    assert details['consumerInputs']['basicAuthPassword'] == 'secret'
    assert details['consumerInputs']['httpHeaders'].splitlines() == [
        'X-OpenHands-Notification-Type:PushNotification',
        'X-OpenHands-Delivery-Probe:token',
    ]
    assert hook == original


@pytest.mark.asyncio
async def test_notification_result_uses_the_installed_subscription_id():
    service = MagicMock(base_url='https://dev.azure.com/contoso')
    service._make_request = AsyncMock(return_value=({'result': 'failed'}, {}))
    await AzureDevOpsWebhooksMixin.get_service_hook_test_notification(
        service, 'installed-hook', 7
    )
    assert (
        service._make_request.call_args.kwargs['url']
        == 'https://dev.azure.com/contoso/_apis/hooks/subscriptions/installed-hook/notifications/7?api-version=7.1'
    )
