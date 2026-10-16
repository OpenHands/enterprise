"""Tests for AzureDevOpsManager.receive_message write-access gating."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from integrations.azure_devops.azure_devops_manager import AzureDevOpsManager
from integrations.azure_devops.azure_devops_service import SaaSAzureDevOpsService
from integrations.azure_devops.azure_devops_v1_callback_processor import (
    AzureDevOpsV1CallbackProcessor,
)
from integrations.azure_devops.azure_devops_view import (
    OPENHANDS_COMMENT_MARKER,
    OPENHANDS_WORK_ITEM_MARKER,
    AzureDevOpsPRComment,
    AzureDevOpsWorkItemComment,
)
from integrations.models import Message, SourceType


def _pr_comment_message(
    body: str = '@openhands please fix this', org: str = 'alonaking'
) -> Message:
    return Message(
        source=SourceType.AZURE_DEVOPS,
        message={
            'event_key': 'ms.vss-code.git-pullrequest-comment-event',
            'payload': {
                'eventType': 'ms.vss-code.git-pullrequest-comment-event',
                'resourceContainers': {
                    'account': {'baseUrl': f'https://dev.azure.com/{org}/'}
                },
                'resource': {
                    'comment': {
                        'id': 2,
                        'author': {
                            'id': 'ado-user-id',
                            'displayName': 'Alice Example',
                            'uniqueName': 'alice@example.com',
                        },
                        'content': body,
                    },
                    'pullRequest': {
                        'pullRequestId': 7,
                        'sourceRefName': 'refs/heads/feature/x',
                        'repository': {
                            'id': 'repo-1',
                            'name': 'Repo',
                            'project': {'id': 'proj-1', 'name': 'Project'},
                        },
                    },
                },
            },
        },
    )


@pytest.mark.asyncio
async def test_receive_message_skips_when_commenter_lacks_write_access(monkeypatch):
    manager = AzureDevOpsManager(AsyncMock())
    manager._resolve_mentioner_keycloak_id = AsyncMock(return_value='kc-alice')  # type: ignore[method-assign]
    manager.start_job = AsyncMock()  # type: ignore[method-assign]

    fake_service = MagicMock()
    fake_service.has_contribute_access = AsyncMock(return_value=False)
    monkeypatch.setattr(
        'integrations.azure_devops.azure_devops_view.AzureDevOpsServiceImpl',
        lambda **kwargs: fake_service,
    )

    await manager.receive_message(_pr_comment_message())

    fake_service.has_contribute_access.assert_awaited_once_with('proj-1', 'repo-1')
    manager.start_job.assert_not_called()


@pytest.mark.asyncio
async def test_receive_message_dispatches_when_commenter_has_write_access(monkeypatch):
    manager = AzureDevOpsManager(AsyncMock())
    manager._resolve_mentioner_keycloak_id = AsyncMock(return_value='kc-alice')  # type: ignore[method-assign]
    manager.start_job = AsyncMock()  # type: ignore[method-assign]

    fake_service = MagicMock()
    fake_service.has_contribute_access = AsyncMock(return_value=True)
    monkeypatch.setattr(
        'integrations.azure_devops.azure_devops_view.AzureDevOpsServiceImpl',
        lambda **kwargs: fake_service,
    )

    await manager.receive_message(_pr_comment_message())

    fake_service.has_contribute_access.assert_awaited_once_with('proj-1', 'repo-1')
    manager.start_job.assert_awaited_once()


def _work_item_comment_message(org: str) -> Message:
    return Message(
        source=SourceType.AZURE_DEVOPS,
        message={
            'event_key': 'workitem.commented',
            'payload': {
                'eventType': 'workitem.commented',
                'resourceContainers': {
                    'account': {'baseUrl': f'https://dev.azure.com/{org}/'}
                },
                'resource': {
                    'id': 42,
                    'fields': {
                        'System.TeamProject': 'Project',
                        'System.ChangedBy': {
                            'id': 'ado-user-id',
                            'displayName': 'Alice Example',
                            'uniqueName': 'alice@example.com',
                        },
                        'System.History': '@openhands please fix work item',
                    },
                },
            },
        },
    )


@pytest.fixture
def azure_requests(monkeypatch):
    """Route resolver calls through the real service and record their URLs."""
    urls: list[str] = []
    state = {'has_access': True}

    async def fake_make_request(self, url, params=None, method=None):
        urls.append(url)
        if '/_apis/security/permissionevaluationbatch' in url:
            return {'evaluations': [{'value': state['has_access']}]}, {}
        if '/_apis/git/repositories' in url:
            repo = {'id': 'repo-b', 'name': 'Repo', 'project': {'id': 'proj-b'}}
            return {'value': [repo]}, {}
        if '/_apis/wit/workitems/42?' in url:
            fields = {'System.Title': 'Beta title', 'System.Description': 'Body'}
            return {'fields': fields}, {}
        if '/_apis/wit/workItems/42/comments' in url:
            return {'comments': []}, {}
        raise AssertionError(f'unexpected Azure request {url}')

    monkeypatch.setattr(SaaSAzureDevOpsService, '_make_request', fake_make_request)
    monkeypatch.setattr(
        'integrations.azure_devops.azure_devops_view.AzureDevOpsServiceImpl',
        SaaSAzureDevOpsService,
    )
    return urls, state


def _manager_with_mentioner() -> AzureDevOpsManager:
    manager = AzureDevOpsManager(AsyncMock())
    manager._resolve_mentioner_keycloak_id = AsyncMock(return_value='kc-alice')  # type: ignore[method-assign]
    manager.start_job = AsyncMock()  # type: ignore[method-assign]
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize('configured_org', ['Alpha', ''])
async def test_pr_comment_from_other_org_checks_access_in_event_org(
    monkeypatch, azure_requests, configured_org
):
    monkeypatch.setenv('AZURE_DEVOPS_ORGANIZATION', configured_org)
    urls, _ = azure_requests
    manager = _manager_with_mentioner()

    await manager.receive_message(_pr_comment_message(org='Beta'))

    manager.start_job.assert_awaited_once()
    assert urls
    assert all(url.startswith('https://dev.azure.com/Beta/') for url in urls)


@pytest.mark.asyncio
@pytest.mark.parametrize('configured_org', ['Alpha', ''])
async def test_work_item_comment_from_other_org_resolves_in_event_org(
    monkeypatch, azure_requests, configured_org
):
    monkeypatch.setenv('AZURE_DEVOPS_ORGANIZATION', configured_org)
    urls, _ = azure_requests
    manager = _manager_with_mentioner()

    await manager.receive_message(_work_item_comment_message('Beta'))

    manager.start_job.assert_awaited_once()
    view = manager.start_job.await_args.args[0]
    assert isinstance(view, AzureDevOpsWorkItemComment)
    assert view.full_repo_name == 'Beta/Project/Repo'
    await view._load_resolver_context()
    assert view.title == 'Beta title'
    assert all(url.startswith('https://dev.azure.com/Beta/') for url in urls)


@pytest.mark.asyncio
async def test_pr_comment_from_other_org_still_denies_without_write_access(
    monkeypatch, azure_requests
):
    monkeypatch.setenv('AZURE_DEVOPS_ORGANIZATION', 'Alpha')
    urls, state = azure_requests
    state['has_access'] = False
    manager = _manager_with_mentioner()

    await manager.receive_message(_pr_comment_message(org='Beta'))

    manager.start_job.assert_not_called()
    assert urls == [
        'https://dev.azure.com/Beta/_apis/security/permissionevaluationbatch'
        '?api-version=7.1-preview.1'
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'changed_by',
    [
        'Alice Example <alice@example.com>',
        # A display name that looks like another user's address must not win.
        'bob@example.com <alice@example.com>',
    ],
)
async def test_work_item_mentioner_resolves_from_identity_string(changed_by):
    # Work item service hooks send System.ChangedBy as "Name <email>", with no revisedBy.
    message = _work_item_comment_message('Beta')
    fields = message.message['payload']['resource']['fields']
    fields['System.ChangedBy'] = changed_by
    token_manager = AsyncMock()
    token_manager.get_user_id_from_user_email = AsyncMock(return_value='kc-alice')
    manager = AzureDevOpsManager(token_manager)

    keycloak_user_id = await manager._resolve_mentioner_keycloak_id(message)

    assert keycloak_user_id == 'kc-alice'
    token_manager.get_user_id_from_user_email.assert_awaited_once_with(
        'alice@example.com'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('is_pr', [True, False])
async def test_replies_carry_the_marker_their_location_keeps(monkeypatch, is_pr):
    service = MagicMock()
    service.add_pr_comment_to_thread = AsyncMock()
    service.add_work_item_comment = AsyncMock()
    monkeypatch.setattr(
        'integrations.azure_devops.azure_devops_manager.AzureDevOpsServiceImpl',
        lambda **kwargs: service,
    )
    view = MagicMock()
    view.__class__ = AzureDevOpsPRComment if is_pr else AzureDevOpsWorkItemComment
    view.user_info.keycloak_user_id = 'kc-alice'
    view.full_repo_name = 'org/Project/Repo'
    view.issue_number = 7
    view.thread_id = 5

    await _manager_with_mentioner().send_message('I am on it', view)

    if is_pr:
        posted = service.add_pr_comment_to_thread.await_args.args[3]
        assert posted.startswith(OPENHANDS_COMMENT_MARKER)
    else:
        posted = service.add_work_item_comment.await_args.args[2]
        assert OPENHANDS_WORK_ITEM_MARKER in posted
    assert posted.endswith('I am on it')


@pytest.mark.asyncio
async def test_work_item_summary_carries_the_work_item_marker(monkeypatch):
    service = MagicMock()
    service.add_work_item_comment = AsyncMock()
    monkeypatch.setattr(
        'openhands.app_server.integrations.azure_devops.azure_devops_service.AzureDevOpsServiceImpl',
        lambda **kwargs: service,
    )
    processor = AzureDevOpsV1CallbackProcessor(
        azure_devops_view_data={
            'keycloak_user_id': 'kc-alice',
            'repository': 'org/Project/Repo',
            'issue_number': 42,
            'is_pr': False,
        }
    )

    await processor._post_summary_to_azure_devops('Done')

    posted = service.add_work_item_comment.await_args.args[2]
    assert OPENHANDS_WORK_ITEM_MARKER in posted
    assert posted.endswith('Done')
