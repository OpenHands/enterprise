import base64
from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from openhands.app_server.integrations.azure_devops.service.base import (
    AzureDevOpsApiError,
)
from openhands.app_server.integrations.service_types import AuthenticationError
from server.routes.integration import azure_devops
from storage.azure_devops_webhook import AzureDevOpsWebhook


class _FakeWebhookStore:
    """In-memory stand-in for AzureDevOpsWebhookStore, keyed like the real one."""

    def __init__(self):
        self.rows: dict[str, AzureDevOpsWebhook] = {}

    async def get_webhook(self, organization):
        return self.rows.get(organization.casefold())

    async def record_install(self, organization, *, user_id, **ids):
        now = datetime.now(timezone.utc)
        return self._upsert(
            organization,
            installed_by_user_id=user_id,
            installed_at=now,
            last_verified_at=now,
            **ids,
        )

    async def record_verified(self, organization, **ids):
        return self._upsert(
            organization, last_verified_at=datetime.now(timezone.utc), **ids
        )

    async def delete_webhook(self, organization):
        return self.rows.pop(organization.casefold(), None) is not None

    def _upsert(self, organization, **values):
        row = self.rows.setdefault(
            organization.casefold(),
            AzureDevOpsWebhook(organization=organization.casefold()),
        )
        for name, value in values.items():
            setattr(row, name, value)
        return row


@pytest.fixture(autouse=True)
def webhook_store(monkeypatch):
    store = _FakeWebhookStore()
    monkeypatch.setattr(azure_devops, 'azure_devops_webhook_store', store)
    return store


@pytest.mark.asyncio
async def test_verify_azure_devops_signature_accepts_header_secret(monkeypatch):
    monkeypatch.setattr(azure_devops, 'IS_LOCAL_DEPLOYMENT', False)
    monkeypatch.setattr(azure_devops, 'AZURE_DEVOPS_WEBHOOK_SECRET', 'expected')

    await azure_devops.verify_azure_devops_signature('expected', None)


@pytest.mark.asyncio
async def test_verify_azure_devops_signature_accepts_basic_auth(monkeypatch):
    monkeypatch.setattr(azure_devops, 'IS_LOCAL_DEPLOYMENT', False)
    monkeypatch.setattr(azure_devops, 'AZURE_DEVOPS_WEBHOOK_SECRET', 'expected')
    encoded = base64.b64encode(b'openhands:expected').decode()

    await azure_devops.verify_azure_devops_signature(None, f'Basic {encoded}')


@pytest.mark.asyncio
async def test_verify_azure_devops_signature_rejects_bad_secret(monkeypatch):
    monkeypatch.setattr(azure_devops, 'IS_LOCAL_DEPLOYMENT', False)
    monkeypatch.setattr(azure_devops, 'AZURE_DEVOPS_WEBHOOK_SECRET', 'expected')

    with pytest.raises(HTTPException) as exc_info:
        await azure_devops.verify_azure_devops_signature('wrong', None)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_azure_devops_resources_reports_installed_status(monkeypatch):
    monkeypatch.setattr(azure_devops, 'HOST_URL', 'https://app.example.com')
    monkeypatch.setattr(azure_devops, 'AZURE_DEVOPS_WEBHOOK_SECRET', 'secret')

    class FakeAzureDevOpsService:
        organization = 'alonaking'

        async def get_accessible_organizations(self):
            return ['alonaking']

        async def list_service_hook_subscriptions(self):
            # Org-wide subscriptions carry no projectId.
            return [
                {
                    'id': 'pr-subscription-id',
                    'eventType': azure_devops.AZURE_DEVOPS_PR_COMMENT_EVENT,
                    'publisherInputs': {},
                    'consumerInputs': {
                        'url': 'https://app.example.com/integration/azure-devops/events',
                    },
                    'status': 'enabled',
                },
                {
                    'id': 'work-item-subscription-id',
                    'eventType': azure_devops.AZURE_DEVOPS_WORK_ITEM_COMMENT_EVENT,
                    'publisherInputs': {},
                    'consumerInputs': {
                        'url': 'https://app.example.com/integration/azure-devops/events',
                    },
                    'status': 'enabled',
                },
            ]

    monkeypatch.setattr(
        azure_devops,
        'SaaSAzureDevOpsService',
        lambda **kwargs: FakeAzureDevOpsService(),
    )

    status = await azure_devops.get_azure_devops_resources(
        user_id='user-id', organization='alonaking'
    )

    assert status.organization == 'alonaking'
    assert status.webhook_installed is True
    assert status.pr_webhook_installed is True
    assert status.work_item_webhook_installed is True
    assert status.pr_subscription_id == 'pr-subscription-id'
    assert status.work_item_subscription_id == 'work-item-subscription-id'
    assert (
        status.webhook_url == 'https://app.example.com/integration/azure-devops/events'
    )


@pytest.mark.asyncio
async def test_get_azure_devops_resources_reports_disabled_hook_as_not_installed(
    monkeypatch,
):
    monkeypatch.setattr(azure_devops, 'HOST_URL', 'https://app.example.com')
    monkeypatch.setattr(azure_devops, 'AZURE_DEVOPS_WEBHOOK_SECRET', 'secret')
    url = 'https://app.example.com/integration/azure-devops/events'

    class FakeAzureDevOpsService:
        organization = 'alonaking'

        async def get_accessible_organizations(self):
            return ['alonaking']

        async def list_service_hook_subscriptions(self):
            return [
                {
                    'id': 'pr-subscription-id',
                    'eventType': azure_devops.AZURE_DEVOPS_PR_COMMENT_EVENT,
                    'publisherInputs': {},
                    'consumerInputs': {'url': url},
                    'status': 'enabled',
                },
                {
                    'id': 'work-item-subscription-id',
                    'eventType': azure_devops.AZURE_DEVOPS_WORK_ITEM_COMMENT_EVENT,
                    'publisherInputs': {},
                    'consumerInputs': {'url': url},
                    'status': 'disabledByUser',
                },
            ]

    monkeypatch.setattr(
        azure_devops,
        'SaaSAzureDevOpsService',
        lambda **kwargs: FakeAzureDevOpsService(),
    )

    status = await azure_devops.get_azure_devops_resources(
        user_id='user-id', organization='alonaking'
    )

    # Disabled work-item hook -> org not fully installed, but id still surfaced.
    assert status.webhook_installed is False
    assert status.pr_webhook_installed is True
    assert status.work_item_webhook_installed is False
    assert status.work_item_subscription_id == 'work-item-subscription-id'


def test_matchers_require_absent_project_id():
    url = 'https://app.example.com/integration/azure-devops/events'
    org_wide = {
        'eventType': azure_devops.AZURE_DEVOPS_PR_COMMENT_EVENT,
        'publisherInputs': {},
        'consumerInputs': {'url': url},
    }
    project_scoped = {
        'eventType': azure_devops.AZURE_DEVOPS_PR_COMMENT_EVENT,
        'publisherInputs': {'projectId': 'proj-guid'},
        'consumerInputs': {'url': url},
    }

    assert (
        azure_devops._matches_pr_comment_subscription(org_wide, webhook_url=url) is True
    )
    assert (
        azure_devops._matches_pr_comment_subscription(project_scoped, webhook_url=url)
        is False
    )


@pytest.mark.asyncio
async def test_get_azure_devops_resources_ignores_project_scoped_hooks(monkeypatch):
    monkeypatch.setattr(azure_devops, 'HOST_URL', 'https://app.example.com')
    monkeypatch.setattr(azure_devops, 'AZURE_DEVOPS_WEBHOOK_SECRET', 'secret')

    url = 'https://app.example.com/integration/azure-devops/events'

    class FakeAzureDevOpsService:
        organization = 'alonaking'

        async def get_accessible_organizations(self):
            return ['alonaking']

        async def list_service_hook_subscriptions(self):
            # Same event types + url, but project-scoped (projectId set) -> must
            # NOT be reported as the org-wide install.
            return [
                {
                    'id': 'pr-project-scoped',
                    'eventType': azure_devops.AZURE_DEVOPS_PR_COMMENT_EVENT,
                    'publisherInputs': {'projectId': 'proj-guid'},
                    'consumerInputs': {'url': url},
                },
                {
                    'id': 'wi-project-scoped',
                    'eventType': azure_devops.AZURE_DEVOPS_WORK_ITEM_COMMENT_EVENT,
                    'publisherInputs': {'projectId': 'proj-guid'},
                    'consumerInputs': {'url': url},
                },
            ]

    monkeypatch.setattr(
        azure_devops,
        'SaaSAzureDevOpsService',
        lambda **kwargs: FakeAzureDevOpsService(),
    )

    status = await azure_devops.get_azure_devops_resources(
        user_id='user-id', organization='alonaking'
    )

    assert status.webhook_installed is False
    assert status.pr_webhook_installed is False
    assert status.work_item_webhook_installed is False
    assert status.pr_subscription_id is None
    assert status.work_item_subscription_id is None


@pytest.mark.asyncio
async def test_reinstall_azure_devops_webhook_replaces_existing_hooks(monkeypatch):
    monkeypatch.setattr(azure_devops, 'HOST_URL', 'https://app.example.com')
    monkeypatch.setattr(azure_devops, 'IS_LOCAL_DEPLOYMENT', False)
    monkeypatch.setattr(azure_devops, 'AZURE_DEVOPS_WEBHOOK_SECRET', 'secret')

    class FakeAzureDevOpsService:
        organization = 'alonaking'

        async def get_accessible_organizations(self):
            return ['alonaking']

        def __init__(self):
            self.delete_service_hook_subscription = AsyncMock()
            self.create_pr_comment_service_hook = AsyncMock(
                return_value={'id': 'new-pr-subscription-id'}
            )
            self.create_work_item_comment_service_hook = AsyncMock(
                return_value={'id': 'new-work-item-subscription-id'}
            )

        async def list_service_hook_subscriptions(self):
            return [
                {
                    'id': 'old-pr-subscription-id',
                    'eventType': azure_devops.AZURE_DEVOPS_PR_COMMENT_EVENT,
                    'publisherInputs': {},
                    'consumerInputs': {
                        'url': 'https://app.example.com/integration/azure-devops/events',
                    },
                },
                {
                    'id': 'old-work-item-subscription-id',
                    'eventType': azure_devops.AZURE_DEVOPS_WORK_ITEM_COMMENT_EVENT,
                    'publisherInputs': {},
                    'consumerInputs': {
                        'url': 'https://app.example.com/integration/azure-devops/events',
                    },
                },
            ]

    fake_service = FakeAzureDevOpsService()
    monkeypatch.setattr(
        azure_devops,
        'SaaSAzureDevOpsService',
        lambda **kwargs: fake_service,
    )

    response = await azure_devops.reinstall_azure_devops_webhook(
        user_id='user-id', organization='alonaking'
    )

    assert response.success is True
    assert response.organization == 'alonaking'
    assert response.pr_subscription_id == 'new-pr-subscription-id'
    assert response.work_item_subscription_id == 'new-work-item-subscription-id'
    fake_service.delete_service_hook_subscription.assert_any_await(
        'old-pr-subscription-id'
    )
    fake_service.delete_service_hook_subscription.assert_any_await(
        'old-work-item-subscription-id'
    )
    fake_service.create_pr_comment_service_hook.assert_awaited_once_with(
        webhook_url='https://app.example.com/integration/azure-devops/events',
        webhook_secret='secret',
    )
    fake_service.create_work_item_comment_service_hook.assert_awaited_once_with(
        webhook_url='https://app.example.com/integration/azure-devops/events',
        webhook_secret='secret',
    )


@pytest.mark.asyncio
async def test_reinstall_azure_devops_webhook_requires_configured_secret(monkeypatch):
    monkeypatch.setattr(azure_devops, 'IS_LOCAL_DEPLOYMENT', False)
    monkeypatch.setattr(azure_devops, 'AZURE_DEVOPS_WEBHOOK_SECRET', '')

    class FakeAzureDevOpsService:
        organization = 'alonaking'

        async def get_accessible_organizations(self):
            return ['alonaking']

    monkeypatch.setattr(
        azure_devops,
        'SaaSAzureDevOpsService',
        lambda **kwargs: FakeAzureDevOpsService(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await azure_devops.reinstall_azure_devops_webhook(
            user_id='user-id', organization='alonaking'
        )

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_events_route_schedules_receive_message_in_background(monkeypatch):
    monkeypatch.setattr(azure_devops, 'IS_LOCAL_DEPLOYMENT', False)
    monkeypatch.setattr(azure_devops, 'AZURE_DEVOPS_WEBHOOK_SECRET', 'secret')

    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=True)
    monkeypatch.setattr(azure_devops, 'get_redis_client_async', lambda: fake_redis)

    fake_manager = MagicMock()
    fake_manager.receive_message = AsyncMock()
    monkeypatch.setattr(azure_devops, 'get_azure_devops_manager', lambda: fake_manager)

    class _FakeRequest:
        async def json(self):
            return {'id': 'evt-1', 'eventType': 'workitem.commented'}

    background_tasks = MagicMock()

    response = await azure_devops.azure_devops_events(
        request=_FakeRequest(),
        background_tasks=background_tasks,
        x_openhands_webhook_secret='secret',
        authorization=None,
    )

    assert response.status_code == 200
    # receive_message is queued, not awaited inline (avoids Service Hooks timeout).
    background_tasks.add_task.assert_called_once_with(fake_manager.receive_message, ANY)
    fake_manager.receive_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_events_route_skips_duplicate_without_scheduling(monkeypatch):
    monkeypatch.setattr(azure_devops, 'IS_LOCAL_DEPLOYMENT', False)
    monkeypatch.setattr(azure_devops, 'AZURE_DEVOPS_WEBHOOK_SECRET', 'secret')

    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=False)  # already seen -> duplicate
    monkeypatch.setattr(azure_devops, 'get_redis_client_async', lambda: fake_redis)

    fake_manager = MagicMock()
    fake_manager.receive_message = AsyncMock()
    monkeypatch.setattr(azure_devops, 'get_azure_devops_manager', lambda: fake_manager)

    class _FakeRequest:
        async def json(self):
            return {'id': 'evt-1', 'eventType': 'workitem.commented'}

    background_tasks = MagicMock()

    response = await azure_devops.azure_devops_events(
        request=_FakeRequest(),
        background_tasks=background_tasks,
        x_openhands_webhook_secret='secret',
        authorization=None,
    )

    assert response.status_code == 200
    background_tasks.add_task.assert_not_called()


_HOOK_URL = 'https://app.example.com/integration/azure-devops/events'


def _hook(subscription_id, event_type, status='enabled'):
    return {
        'id': subscription_id,
        'status': status,
        'eventType': event_type,
        'publisherInputs': {},
        'consumerInputs': {'url': _HOOK_URL},
    }


class _InMemoryAzureOrg:
    """Fake service that tracks which Service Hooks exist and call order."""

    organization = 'alonaking'

    def __init__(self, *, fail_create=None, fail_delete=()):
        self.hooks = {
            'old-pr': _hook('old-pr', azure_devops.AZURE_DEVOPS_PR_COMMENT_EVENT),
            'old-wi': _hook(
                'old-wi', azure_devops.AZURE_DEVOPS_WORK_ITEM_COMMENT_EVENT
            ),
        }
        self.fail_create = fail_create
        self.fail_delete = set(fail_delete)
        self.calls = []

    async def list_service_hook_subscriptions(self):
        return list(self.hooks.values())

    async def get_service_hook_permissions(self):
        return True, True

    async def delete_service_hook_subscription(self, subscription_id):
        self.calls.append(('delete', subscription_id))
        if subscription_id in self.fail_delete:
            raise RuntimeError('delete failed')
        self.hooks.pop(subscription_id)

    def _create(self, kind, event_type):
        self.calls.append(('create', kind))
        if self.fail_create == kind:
            raise AzureDevOpsApiError(
                400, 'The user does not have permission to edit subscriptions.'
            )
        subscription_id = f'new-{kind}'
        self.hooks[subscription_id] = _hook(subscription_id, event_type)
        return {'id': subscription_id}

    async def create_pr_comment_service_hook(self, **_):
        return self._create('pr', azure_devops.AZURE_DEVOPS_PR_COMMENT_EVENT)

    async def create_work_item_comment_service_hook(self, **_):
        return self._create('wi', azure_devops.AZURE_DEVOPS_WORK_ITEM_COMMENT_EVENT)


def _use_service(monkeypatch, service):
    monkeypatch.setattr(azure_devops, 'HOST_URL', 'https://app.example.com')
    monkeypatch.setattr(azure_devops, 'IS_LOCAL_DEPLOYMENT', False)
    monkeypatch.setattr(azure_devops, 'AZURE_DEVOPS_WEBHOOK_SECRET', 'secret')
    service.get_accessible_organizations = AsyncMock(return_value=['alonaking'])
    monkeypatch.setattr(
        azure_devops, 'SaaSAzureDevOpsService', lambda external_auth_id: service
    )


@pytest.mark.asyncio
async def test_reinstall_creates_new_hooks_before_deleting_old_ones(monkeypatch):
    service = _InMemoryAzureOrg()
    _use_service(monkeypatch, service)

    response = await azure_devops.reinstall_azure_devops_webhook(
        user_id='user-id', organization='alonaking'
    )

    assert response.success is True
    assert set(service.hooks) == {'new-pr', 'new-wi'}
    assert service.calls == [
        ('create', 'pr'),
        ('create', 'wi'),
        ('delete', 'old-pr'),
        ('delete', 'old-wi'),
    ]


@pytest.mark.asyncio
async def test_reinstall_keeps_existing_hooks_when_azure_rejects_create(monkeypatch):
    service = _InMemoryAzureOrg(fail_create='pr')
    _use_service(monkeypatch, service)

    with pytest.raises(HTTPException) as exc_info:
        await azure_devops.reinstall_azure_devops_webhook(
            user_id='user-id', organization='alonaking'
        )

    assert exc_info.value.status_code == 502
    assert 'HTTP 400' in exc_info.value.detail
    assert (
        'The user does not have permission to edit subscriptions.'
        in exc_info.value.detail
    )
    assert set(service.hooks) == {'old-pr', 'old-wi'}


@pytest.mark.asyncio
async def test_reinstall_rolls_back_new_hook_when_second_create_fails(monkeypatch):
    service = _InMemoryAzureOrg(fail_create='wi')
    _use_service(monkeypatch, service)

    with pytest.raises(HTTPException) as exc_info:
        await azure_devops.reinstall_azure_devops_webhook(
            user_id='user-id', organization='alonaking'
        )

    assert exc_info.value.status_code == 502
    assert set(service.hooks) == {'old-pr', 'old-wi'}
    assert ('delete', 'new-pr') in service.calls


@pytest.mark.asyncio
async def test_reinstall_succeeds_when_old_hook_cleanup_fails(monkeypatch):
    service = _InMemoryAzureOrg(fail_delete={'old-pr'})
    _use_service(monkeypatch, service)

    response = await azure_devops.reinstall_azure_devops_webhook(
        user_id='user-id', organization='alonaking'
    )

    assert response.success is True
    assert response.pr_subscription_id == 'new-pr'
    assert set(service.hooks) == {'old-pr', 'new-pr', 'new-wi'}


@pytest.mark.asyncio
async def test_resources_prefers_enabled_hook_over_stale_copy(monkeypatch):
    service = _InMemoryAzureOrg()
    service.hooks = {
        'stale-pr': _hook(
            'stale-pr', azure_devops.AZURE_DEVOPS_PR_COMMENT_EVENT, 'disabledBySystem'
        ),
        'new-pr': _hook('new-pr', azure_devops.AZURE_DEVOPS_PR_COMMENT_EVENT),
        'new-wi': _hook('new-wi', azure_devops.AZURE_DEVOPS_WORK_ITEM_COMMENT_EVENT),
    }
    _use_service(monkeypatch, service)

    status = await azure_devops.get_azure_devops_resources(
        user_id='user-id', organization='alonaking'
    )

    assert status.webhook_installed is True
    assert status.pr_subscription_id == 'new-pr'


@pytest.mark.asyncio
async def test_uninstall_reports_azure_error_message(monkeypatch):
    service = _InMemoryAzureOrg()

    async def reject_delete(subscription_id):
        raise AzureDevOpsApiError(403, 'Access denied.')

    service.delete_service_hook_subscription = reject_delete
    _use_service(monkeypatch, service)

    with pytest.raises(HTTPException) as exc_info:
        await azure_devops.uninstall_azure_devops_webhook(
            user_id='user-id', organization='alonaking'
        )

    assert exc_info.value.status_code == 502
    assert 'Access denied.' in exc_info.value.detail


@pytest.fixture
def organization_service(monkeypatch):
    service = MagicMock()
    service.organization = 'configured-org'
    service.get_accessible_organizations = AsyncMock(return_value=['Alpha', 'Beta'])
    service.list_service_hook_subscriptions = AsyncMock(return_value=[])
    service.get_service_hook_permissions = AsyncMock(return_value=(True, True))
    service.delete_service_hook_subscription = AsyncMock()
    service.create_pr_comment_service_hook = AsyncMock(return_value={'id': 'pr'})
    service.create_work_item_comment_service_hook = AsyncMock(return_value={'id': 'wi'})
    monkeypatch.setattr(
        azure_devops, 'SaaSAzureDevOpsService', lambda **kwargs: service
    )
    monkeypatch.setattr(azure_devops, 'AZURE_DEVOPS_WEBHOOK_SECRET', 'test-secret')
    return service


@pytest.mark.asyncio
async def test_organizations_list_uses_membership_not_configured_default(
    organization_service,
):
    result = await azure_devops.get_azure_devops_organizations(user_id='user')
    assert result.organizations == ['Alpha', 'Beta']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'operation',
    [
        azure_devops.get_azure_devops_resources,
        azure_devops.reinstall_azure_devops_webhook,
        azure_devops.uninstall_azure_devops_webhook,
    ],
)
@pytest.mark.parametrize('requested,expected', [(' alpha ', 'Alpha'), ('BETA', 'Beta')])
async def test_operations_use_explicit_canonical_organization(
    organization_service, operation, requested, expected
):
    result = await operation(user_id='user', organization=requested)
    assert result.organization == expected
    assert organization_service.organization == expected
    organization_service.get_accessible_organizations.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'operation',
    [
        azure_devops.get_azure_devops_resources,
        azure_devops.reinstall_azure_devops_webhook,
        azure_devops.uninstall_azure_devops_webhook,
    ],
)
@pytest.mark.parametrize(
    'requested,code',
    [
        ('configured-org', 403),
        ('https://attacker.invalid', 403),
        (' ', 400),
    ],
)
async def test_inaccessible_organization_never_reads_or_changes_hooks(
    organization_service, operation, requested, code
):
    with pytest.raises(HTTPException) as error:
        await operation(user_id='user', organization=requested)
    assert error.value.status_code == code
    organization_service.list_service_hook_subscriptions.assert_not_awaited()
    organization_service.delete_service_hook_subscription.assert_not_awaited()
    organization_service.create_pr_comment_service_hook.assert_not_awaited()
    organization_service.create_work_item_comment_service_hook.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'operation',
    [
        azure_devops.get_azure_devops_resources,
        azure_devops.reinstall_azure_devops_webhook,
        azure_devops.uninstall_azure_devops_webhook,
    ],
)
async def test_membership_failure_blocks_hook_operations(
    organization_service, operation
):
    organization_service.get_accessible_organizations.side_effect = RuntimeError(
        'Azure unavailable'
    )
    with pytest.raises(HTTPException) as error:
        await operation(user_id='user', organization='Alpha')
    assert error.value.status_code == 503
    organization_service.list_service_hook_subscriptions.assert_not_awaited()


@pytest.mark.asyncio
async def test_sequential_users_cannot_reuse_another_users_membership(monkeypatch):
    memberships = {
        'shared-user': ['Alpha', 'Beta'],
        'alpha-user': ['Alpha'],
        'beta-user': ['Beta'],
    }
    services = []

    def make_service(external_auth_id):
        service = MagicMock()
        service.organization = ''
        service.get_accessible_organizations = AsyncMock(
            return_value=memberships[external_auth_id]
        )
        service.list_service_hook_subscriptions = AsyncMock(return_value=[])
        services.append(service)
        return service

    monkeypatch.setattr(azure_devops, 'SaaSAzureDevOpsService', make_service)
    for user, org in [
        ('shared-user', 'Alpha'),
        ('shared-user', 'Beta'),
        ('alpha-user', 'Alpha'),
        ('beta-user', 'Beta'),
    ]:
        result = await azure_devops.get_azure_devops_resources(
            user_id=user, organization=org
        )
        assert result.organization == org
    with pytest.raises(HTTPException) as error:
        await azure_devops.get_azure_devops_resources(
            user_id='alpha-user', organization='Beta'
        )
    assert error.value.status_code == 403
    services[-1].list_service_hook_subscriptions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('organization', [None, ' '])
async def test_scoped_routes_require_an_organization(
    organization_service, organization
):
    with pytest.raises(HTTPException) as exc_info:
        await azure_devops.get_azure_devops_resources(
            user_id='user', organization=organization
        )

    assert exc_info.value.status_code == 400
    organization_service.get_accessible_organizations.assert_not_awaited()


def _with_permissions(service, *, view=True, edit=True):
    service.get_service_hook_permissions = AsyncMock(return_value=(view, edit))
    return service


@pytest.mark.asyncio
async def test_resources_reports_installed_hooks_for_member_who_can_manage(
    monkeypatch,
):
    service = _with_permissions(_InMemoryAzureOrg())
    _use_service(monkeypatch, service)

    status = await azure_devops.get_azure_devops_resources(
        user_id='user-id', organization='alonaking'
    )

    assert status.webhook_installed is True
    assert status.pr_subscription_id == 'old-pr'
    assert status.can_view_webhooks is True
    assert status.can_manage_webhooks is True


@pytest.mark.asyncio
async def test_resources_reports_not_installed_when_visible_list_is_empty(
    monkeypatch,
):
    service = _with_permissions(_InMemoryAzureOrg())
    service.hooks = {}
    _use_service(monkeypatch, service)

    status = await azure_devops.get_azure_devops_resources(
        user_id='user-id', organization='alonaking'
    )

    assert status.webhook_installed is False
    assert status.pr_webhook_installed is False
    assert status.work_item_webhook_installed is False
    assert status.can_view_webhooks is True
    assert status.can_manage_webhooks is True


@pytest.mark.asyncio
async def test_resources_does_not_trust_empty_list_without_view_permission(
    monkeypatch,
):
    # Azure returns an empty list to members who can't view Service Hooks,
    # even though the org-wide hooks are installed.
    service = _with_permissions(_InMemoryAzureOrg(), view=False, edit=False)
    service.list_service_hook_subscriptions = AsyncMock(return_value=[])
    _use_service(monkeypatch, service)

    status = await azure_devops.get_azure_devops_resources(
        user_id='user-id', organization='alonaking'
    )

    assert status.can_view_webhooks is False
    assert status.can_manage_webhooks is False
    assert status.webhook_installed is False
    assert status.pr_subscription_id is None
    service.list_service_hook_subscriptions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error',
    [
        AuthenticationError('Invalid azure_devops token'),
        AzureDevOpsApiError(403, 'Access denied.'),
    ],
)
async def test_resources_reports_unverifiable_when_azure_denies_listing(
    monkeypatch, error
):
    service = _with_permissions(_InMemoryAzureOrg())
    service.list_service_hook_subscriptions = AsyncMock(side_effect=error)
    _use_service(monkeypatch, service)

    status = await azure_devops.get_azure_devops_resources(
        user_id='user-id', organization='alonaking'
    )

    assert status.can_view_webhooks is False
    assert status.can_manage_webhooks is False
    assert status.webhook_installed is False


@pytest.mark.asyncio
async def test_resources_reports_status_but_no_actions_for_view_only_member(
    monkeypatch,
):
    service = _with_permissions(_InMemoryAzureOrg(), view=True, edit=False)
    _use_service(monkeypatch, service)

    status = await azure_devops.get_azure_devops_resources(
        user_id='user-id', organization='alonaking'
    )

    assert status.webhook_installed is True
    assert status.can_view_webhooks is True
    assert status.can_manage_webhooks is False


@pytest.mark.asyncio
async def test_resources_keeps_listing_status_when_permissions_are_unknown(
    monkeypatch,
):
    service = _InMemoryAzureOrg()
    service.get_service_hook_permissions = AsyncMock(
        side_effect=AzureDevOpsApiError(400, 'Bad request.')
    )
    _use_service(monkeypatch, service)

    status = await azure_devops.get_azure_devops_resources(
        user_id='user-id', organization='alonaking'
    )

    assert status.webhook_installed is True
    assert status.can_view_webhooks is True
    assert status.can_manage_webhooks is True


@pytest.mark.asyncio
async def test_resources_still_reports_other_azure_listing_errors(monkeypatch):
    service = _with_permissions(_InMemoryAzureOrg())
    service.list_service_hook_subscriptions = AsyncMock(
        side_effect=AzureDevOpsApiError(500, 'Service unavailable.')
    )
    _use_service(monkeypatch, service)

    with pytest.raises(HTTPException) as exc_info:
        await azure_devops.get_azure_devops_resources(
            user_id='user-id', organization='alonaking'
        )

    assert exc_info.value.status_code == 502
    assert 'Service unavailable.' in exc_info.value.detail


@pytest.mark.asyncio
async def test_reinstall_records_who_installed_the_hooks_and_when(
    monkeypatch, webhook_store
):
    _use_service(monkeypatch, _InMemoryAzureOrg())

    await azure_devops.reinstall_azure_devops_webhook(
        user_id='owner-id', organization='alonaking'
    )

    record = webhook_store.rows['alonaking']
    assert record.installed_by_user_id == 'owner-id'
    assert record.installed_at is not None
    assert record.last_verified_at == record.installed_at
    assert (record.pr_subscription_id, record.work_item_subscription_id) == (
        'new-pr',
        'new-wi',
    )


@pytest.mark.asyncio
async def test_uninstall_deletes_the_install_record(monkeypatch, webhook_store):
    _use_service(monkeypatch, _InMemoryAzureOrg())
    await webhook_store.record_install(
        'alonaking',
        user_id='owner-id',
        pr_subscription_id='old-pr',
        work_item_subscription_id='old-wi',
    )

    await azure_devops.uninstall_azure_devops_webhook(
        user_id='owner-id', organization='alonaking'
    )

    assert webhook_store.rows == {}


@pytest.mark.asyncio
async def test_member_without_view_permission_sees_the_recorded_install(
    monkeypatch, webhook_store
):
    service = _with_permissions(_InMemoryAzureOrg(), view=False, edit=False)
    service.list_service_hook_subscriptions = AsyncMock(return_value=[])
    _use_service(monkeypatch, service)
    record = await webhook_store.record_install(
        'AlonaKing',
        user_id='owner-id',
        pr_subscription_id='old-pr',
        work_item_subscription_id='old-wi',
    )

    status = await azure_devops.get_azure_devops_resources(
        user_id='member-id', organization='alonaking'
    )

    assert status.can_view_webhooks is False
    assert status.install_recorded is True
    assert status.installed_at == record.installed_at
    assert status.last_verified_at == record.last_verified_at
    assert webhook_store.rows['alonaking'] is record
    service.list_service_hook_subscriptions.assert_not_awaited()


@pytest.mark.asyncio
async def test_member_without_view_permission_and_no_record_sees_none(monkeypatch):
    service = _with_permissions(_InMemoryAzureOrg(), view=False, edit=False)
    _use_service(monkeypatch, service)

    status = await azure_devops.get_azure_devops_resources(
        user_id='member-id', organization='alonaking'
    )

    assert status.install_recorded is False
    assert status.installed_at is None


@pytest.mark.asyncio
async def test_viewer_who_sees_installed_hooks_backfills_the_record(
    monkeypatch, webhook_store
):
    service = _with_permissions(_InMemoryAzureOrg())
    _use_service(monkeypatch, service)

    status = await azure_devops.get_azure_devops_resources(
        user_id='owner-id', organization='alonaking'
    )

    record = webhook_store.rows['alonaking']
    assert status.install_recorded is True
    # Installed outside OpenHands (or before records existed): no install date.
    assert record.installed_at is None and status.installed_at is None
    assert record.last_verified_at is not None
    assert status.last_verified_at == record.last_verified_at


@pytest.mark.asyncio
async def test_viewer_keeps_original_install_details_when_reverifying(
    monkeypatch, webhook_store
):
    service = _with_permissions(_InMemoryAzureOrg())
    _use_service(monkeypatch, service)
    installed = datetime(2026, 10, 20, tzinfo=timezone.utc)
    record = await webhook_store.record_install(
        'alonaking',
        user_id='owner-id',
        pr_subscription_id='old-pr',
        work_item_subscription_id='old-wi',
    )
    record.installed_at = record.last_verified_at = installed

    status = await azure_devops.get_azure_devops_resources(
        user_id='other-owner-id', organization='alonaking'
    )

    assert status.installed_at == installed
    assert status.last_verified_at > installed
    assert record.installed_by_user_id == 'owner-id'


@pytest.mark.asyncio
async def test_confirmed_viewer_who_sees_no_hooks_clears_the_record(
    monkeypatch, webhook_store
):
    service = _with_permissions(_InMemoryAzureOrg())
    service.hooks = {}
    _use_service(monkeypatch, service)
    await webhook_store.record_install(
        'alonaking',
        user_id='owner-id',
        pr_subscription_id='old-pr',
        work_item_subscription_id='old-wi',
    )

    status = await azure_devops.get_azure_devops_resources(
        user_id='owner-id', organization='alonaking'
    )

    assert status.webhook_installed is False
    assert status.install_recorded is False
    assert webhook_store.rows == {}


@pytest.mark.asyncio
async def test_unconfirmed_empty_listing_does_not_clear_the_record(
    monkeypatch, webhook_store
):
    # The permission check failed, so an empty list may only mean the user
    # can't see the hooks.
    service = _InMemoryAzureOrg()
    service.hooks = {}
    service.get_service_hook_permissions = AsyncMock(
        side_effect=AzureDevOpsApiError(400, 'Bad request.')
    )
    _use_service(monkeypatch, service)
    await webhook_store.record_install(
        'alonaking',
        user_id='owner-id',
        pr_subscription_id='old-pr',
        work_item_subscription_id='old-wi',
    )

    status = await azure_devops.get_azure_devops_resources(
        user_id='member-id', organization='alonaking'
    )

    assert status.install_recorded is True
    assert 'alonaking' in webhook_store.rows


@pytest.mark.asyncio
async def test_status_still_returns_when_the_record_store_fails(monkeypatch):
    service = _with_permissions(_InMemoryAzureOrg())
    _use_service(monkeypatch, service)
    broken = MagicMock()
    broken.record_verified = AsyncMock(side_effect=RuntimeError('db down'))
    monkeypatch.setattr(azure_devops, 'azure_devops_webhook_store', broken)

    status = await azure_devops.get_azure_devops_resources(
        user_id='owner-id', organization='alonaking'
    )

    assert status.webhook_installed is True
    assert status.install_recorded is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'remaining',
    [
        # Both hooks still exist but Azure paused deliveries.
        {
            'old-pr': _hook(
                'old-pr', azure_devops.AZURE_DEVOPS_PR_COMMENT_EVENT, 'onProbation'
            ),
            'old-wi': _hook(
                'old-wi',
                azure_devops.AZURE_DEVOPS_WORK_ITEM_COMMENT_EVENT,
                'onProbation',
            ),
        },
        {
            'old-pr': _hook(
                'old-pr', azure_devops.AZURE_DEVOPS_PR_COMMENT_EVENT, 'disabledBySystem'
            ),
            'old-wi': _hook(
                'old-wi', azure_devops.AZURE_DEVOPS_WORK_ITEM_COMMENT_EVENT
            ),
        },
        # Only one of the two hooks is left.
        {'old-pr': _hook('old-pr', azure_devops.AZURE_DEVOPS_PR_COMMENT_EVENT)},
    ],
)
async def test_hooks_that_still_exist_keep_the_install_record(
    monkeypatch, webhook_store, remaining
):
    service = _with_permissions(_InMemoryAzureOrg())
    service.hooks = remaining
    _use_service(monkeypatch, service)
    installed = datetime(2026, 10, 20, tzinfo=timezone.utc)
    record = await webhook_store.record_install(
        'alonaking',
        user_id='owner-id',
        pr_subscription_id='old-pr',
        work_item_subscription_id='old-wi',
    )
    record.installed_at = record.last_verified_at = installed

    status = await azure_devops.get_azure_devops_resources(
        user_id='owner-id', organization='alonaking'
    )

    assert status.webhook_installed is False
    assert status.install_recorded is True
    assert status.installed_at == installed
    # Not reconfirmed while the hooks aren't delivering.
    assert status.last_verified_at == installed
    assert webhook_store.rows['alonaking'].installed_by_user_id == 'owner-id'
