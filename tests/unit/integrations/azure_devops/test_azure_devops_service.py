"""Unit tests for SaaSAzureDevOpsService."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from integrations.azure_devops.azure_devops_service import SaaSAzureDevOpsService
from openhands.app_server.integrations.azure_devops.service.base import (
    AzureDevOpsApiError,
)
from openhands.app_server.integrations.service_types import (
    AuthenticationError,
    ProviderType,
    RequestMethod,
    UnknownException,
)


@pytest.mark.asyncio
async def test_get_latest_token_updates_cached_token_for_retry_headers():
    service = SaaSAzureDevOpsService(
        external_auth_token=SecretStr('keycloak-token'),
        token=SecretStr('expired-token'),
    )

    with patch.object(
        service.token_manager,
        'get_idp_token',
        new_callable=AsyncMock,
        return_value='fresh-token',
    ) as mock_get_idp_token:
        token = await service.get_latest_token()

    assert token is not None
    assert token.get_secret_value() == 'fresh-token'
    assert service.token.get_secret_value() == 'fresh-token'
    mock_get_idp_token.assert_awaited_once_with(
        'keycloak-token',
        idp=ProviderType.AZURE_DEVOPS,
    )


@pytest.mark.asyncio
async def test_get_latest_token_updates_cached_token_from_external_auth_id():
    service = SaaSAzureDevOpsService(
        external_auth_id='external-auth-id',
        token=SecretStr('expired-token'),
    )

    with patch.object(
        service.token_manager,
        'get_idp_token_for_user',
        new_callable=AsyncMock,
        return_value='fresh-token',
    ) as mock_get_idp_token_for_user:
        token = await service.get_latest_token()

    assert token is not None
    assert token.get_secret_value() == 'fresh-token'
    assert service.token.get_secret_value() == 'fresh-token'
    mock_get_idp_token_for_user.assert_awaited_once_with(
        'external-auth-id', ProviderType.AZURE_DEVOPS
    )


@pytest.mark.asyncio
async def test_get_latest_token_updates_cached_token_from_user_id():
    service = SaaSAzureDevOpsService(
        user_id='azure-user-id',
        token=SecretStr('expired-token'),
    )

    with patch.object(
        service.token_manager,
        'get_idp_token_from_idp_user_id',
        new_callable=AsyncMock,
        return_value='fresh-token',
    ) as mock_get_idp_token_from_user_id:
        token = await service.get_latest_token()

    assert token is not None
    assert token.get_secret_value() == 'fresh-token'
    assert service.token.get_secret_value() == 'fresh-token'
    mock_get_idp_token_from_user_id.assert_awaited_once_with(
        'azure-user-id',
        ProviderType.AZURE_DEVOPS,
    )


@pytest.mark.asyncio
async def test_get_latest_token_leaves_cached_token_when_refresh_unavailable():
    service = SaaSAzureDevOpsService(token=SecretStr('stored-token'))

    token = await service.get_latest_token()

    assert token is None
    assert service.token.get_secret_value() == 'stored-token'


@pytest.mark.asyncio
async def test_pr_comment_urls_do_not_duplicate_organization():
    service = SaaSAzureDevOpsService(token=SecretStr('token'), base_domain='alonaking')
    service._make_request = AsyncMock(return_value=({'id': 1}, {}))  # type: ignore[method-assign]

    await service.add_pr_comment_to_thread(
        'alonaking/My Project/My Repo',
        12,
        5,
        'hello',
    )

    url = service._make_request.await_args.kwargs['url']
    assert url.startswith(
        'https://dev.azure.com/alonaking/My%20Project/_apis/git/repositories/My%20Repo/'
    )
    assert 'alonaking/alonaking' not in url


@pytest.mark.asyncio
async def test_work_item_comment_urls_do_not_duplicate_organization():
    service = SaaSAzureDevOpsService(token=SecretStr('token'), base_domain='alonaking')
    service._make_request = AsyncMock(return_value=({'id': 1}, {}))  # type: ignore[method-assign]

    await service.add_work_item_comment(
        'alonaking/My Project/My Repo',
        42,
        'hello',
    )

    url = service._make_request.await_args.kwargs['url']
    assert url.startswith(
        'https://dev.azure.com/alonaking/My%20Project/_apis/wit/workItems/42/'
    )
    assert 'alonaking/alonaking' not in url


@pytest.mark.asyncio
async def test_make_request_accepts_successful_empty_response_body():
    service = SaaSAzureDevOpsService(token=SecretStr('token'), base_domain='alonaking')
    request = httpx.Request('DELETE', 'https://dev.azure.com/alonaking/_apis/hooks/1')
    response = httpx.Response(204, request=request)

    with patch.object(
        service,
        'execute_request',
        new_callable=AsyncMock,
        return_value=response,
    ):
        body, headers = await service._make_request(
            url='https://dev.azure.com/alonaking/_apis/hooks/1',
            method=RequestMethod.DELETE,
        )

    assert body == {}
    assert headers == {}


@pytest.mark.asyncio
async def test_make_request_keeps_azure_error_message():
    service = SaaSAzureDevOpsService(token=SecretStr('token'), base_domain='alonaking')
    url = 'https://dev.azure.com/alonaking/_apis/hooks/subscriptions?api-version=7.1'
    response = httpx.Response(
        400,
        json={
            'message': 'The user does not have permission to edit subscriptions.',
            'typeKey': 'SubscriptionException',
        },
        request=httpx.Request('POST', url),
    )

    with patch.object(
        service,
        'execute_request',
        new_callable=AsyncMock,
        return_value=response,
    ):
        with pytest.raises(AzureDevOpsApiError) as exc_info:
            await service._make_request(url=url, method=RequestMethod.POST)

    assert exc_info.value.status_code == 400
    assert exc_info.value.azure_message == (
        'The user does not have permission to edit subscriptions.'
    )
    assert exc_info.value.type_key == 'SubscriptionException'


@pytest.mark.asyncio
async def test_make_request_handles_non_json_azure_error():
    service = SaaSAzureDevOpsService(token=SecretStr('token'), base_domain='alonaking')
    url = 'https://dev.azure.com/alonaking/_apis/hooks/subscriptions?api-version=7.1'
    response = httpx.Response(
        503, text='Service Unavailable', request=httpx.Request('POST', url)
    )

    with patch.object(
        service,
        'execute_request',
        new_callable=AsyncMock,
        return_value=response,
    ):
        with pytest.raises(AzureDevOpsApiError) as exc_info:
            await service._make_request(url=url, method=RequestMethod.POST)

    assert exc_info.value.status_code == 503
    assert exc_info.value.azure_message == 'Service Unavailable'


@pytest.mark.asyncio
async def test_make_request_still_maps_unauthorized_to_authentication_error():
    service = SaaSAzureDevOpsService(token=SecretStr('token'), base_domain='alonaking')
    url = 'https://dev.azure.com/alonaking/_apis/hooks/subscriptions?api-version=7.1'
    response = httpx.Response(
        401, json={'message': 'Unauthorized'}, request=httpx.Request('GET', url)
    )

    with patch.object(
        service,
        'execute_request',
        new_callable=AsyncMock,
        return_value=response,
    ):
        with pytest.raises(AuthenticationError):
            await service._make_request(url=url)


@pytest.mark.asyncio
async def test_create_pr_comment_service_hook_posts_expected_payload():
    service = SaaSAzureDevOpsService(token=SecretStr('token'), base_domain='alonaking')
    service._make_request = AsyncMock(  # type: ignore[method-assign]
        return_value=({'id': 'subscription-id'}, {})
    )

    response = await service.create_pr_comment_service_hook(
        webhook_url='https://app.example.com/integration/azure-devops/events',
        webhook_secret='secret',
    )

    assert response == {'id': 'subscription-id'}
    kwargs = service._make_request.await_args.kwargs
    assert kwargs['url'] == (
        'https://dev.azure.com/alonaking/_apis/hooks/subscriptions?api-version=7.1'
    )
    assert kwargs['method'] == RequestMethod.POST
    assert kwargs['params']['eventType'] == 'ms.vss-code.git-pullrequest-comment-event'
    assert kwargs['params']['resourceVersion'] == '2.0'
    assert kwargs['params']['publisherInputs'] == {}
    assert kwargs['params']['consumerInputs']['url'] == (
        'https://app.example.com/integration/azure-devops/events'
    )
    assert kwargs['params']['consumerInputs']['basicAuthUsername'] == 'openhands'
    assert kwargs['params']['consumerInputs']['basicAuthPassword'] == 'secret'


@pytest.mark.asyncio
async def test_has_contribute_access_posts_repo_token_and_returns_evaluation():
    service = SaaSAzureDevOpsService(token=SecretStr('token'), base_domain='alonaking')
    service._make_request = AsyncMock(  # type: ignore[method-assign]
        return_value=({'evaluations': [{'value': True}]}, {})
    )

    result = await service.has_contribute_access('proj-1', 'repo-1')

    assert result is True
    kwargs = service._make_request.await_args.kwargs
    assert kwargs['url'] == (
        'https://dev.azure.com/alonaking/_apis/security/permissionevaluationbatch'
        '?api-version=7.1-preview.1'
    )
    assert kwargs['method'] == RequestMethod.POST
    evaluation = kwargs['params']['evaluations'][0]
    assert evaluation['token'] == 'repoV2/proj-1/repo-1'
    assert evaluation['permissions'] == 4


@pytest.mark.asyncio
async def test_has_contribute_access_fails_closed_on_missing_ids():
    service = SaaSAzureDevOpsService(token=SecretStr('token'), base_domain='alonaking')
    service._make_request = AsyncMock()  # type: ignore[method-assign]

    assert await service.has_contribute_access('', 'repo-1') is False
    service._make_request.assert_not_called()


@pytest.mark.asyncio
async def test_has_contribute_access_fails_closed_on_error():
    service = SaaSAzureDevOpsService(token=SecretStr('token'), base_domain='alonaking')
    service._make_request = AsyncMock(side_effect=RuntimeError('boom'))  # type: ignore[method-assign]

    assert await service.has_contribute_access('proj-1', 'repo-1') is False


@pytest.mark.asyncio
async def test_get_service_hook_permissions_evaluates_org_view_and_edit():
    service = SaaSAzureDevOpsService(token=SecretStr('token'), base_domain='alonaking')
    service._make_request = AsyncMock(  # type: ignore[method-assign]
        return_value=({'evaluations': [{'value': True}, {'value': False}]}, {})
    )

    assert await service.get_service_hook_permissions() == (True, False)
    kwargs = service._make_request.await_args.kwargs
    assert kwargs['url'] == (
        'https://dev.azure.com/alonaking/_apis/security/permissionevaluationbatch'
        '?api-version=7.1-preview.1'
    )
    assert kwargs['method'] == RequestMethod.POST
    assert kwargs['params']['alwaysAllowAdministrators'] is True
    assert [
        (e['securityNamespaceId'], e['token'], e['permissions'])
        for e in kwargs['params']['evaluations']
    ] == [
        ('cb594ebe-87dd-4fc9-ac2c-6a10a4c92046', 'PublisherSecurity', 1),
        ('cb594ebe-87dd-4fc9-ac2c-6a10a4c92046', 'PublisherSecurity', 2),
    ]


@pytest.mark.asyncio
async def test_get_service_hook_permissions_raises_on_incomplete_response():
    service = SaaSAzureDevOpsService(token=SecretStr('token'), base_domain='alonaking')
    service._make_request = AsyncMock(  # type: ignore[method-assign]
        return_value=({'evaluations': [{'value': True}]}, {})
    )

    with pytest.raises(ValueError):
        await service.get_service_hook_permissions()


@pytest.mark.asyncio
async def test_get_project_repositories_returns_value_list():
    service = SaaSAzureDevOpsService(token=SecretStr('token'), base_domain='alonaking')
    service._make_request = AsyncMock(  # type: ignore[method-assign]
        return_value=({'value': [{'id': 'repo-1', 'name': 'svc'}]}, {})
    )

    repos = await service.get_project_repositories('My Project')

    assert repos == [{'id': 'repo-1', 'name': 'svc'}]
    call = service._make_request.await_args
    url = call.kwargs.get('url') or call.args[0]
    assert url == (
        'https://dev.azure.com/alonaking/My%20Project/_apis/git/repositories'
        '?api-version=7.1'
    )


@pytest.mark.asyncio
async def test_create_work_item_comment_service_hook_posts_expected_payload():
    service = SaaSAzureDevOpsService(token=SecretStr('token'), base_domain='alonaking')
    service._make_request = AsyncMock(  # type: ignore[method-assign]
        return_value=({'id': 'subscription-id'}, {})
    )

    response = await service.create_work_item_comment_service_hook(
        webhook_url='https://app.example.com/integration/azure-devops/events',
        webhook_secret='secret',
    )

    assert response == {'id': 'subscription-id'}
    kwargs = service._make_request.await_args.kwargs
    assert kwargs['url'] == (
        'https://dev.azure.com/alonaking/_apis/hooks/subscriptions?api-version=7.1'
    )
    assert kwargs['method'] == RequestMethod.POST
    assert kwargs['params']['eventType'] == 'workitem.commented'
    assert kwargs['params']['resourceVersion'] == '1.0'
    assert kwargs['params']['publisherInputs'] == {}
    assert kwargs['params']['consumerInputs']['basicAuthUsername'] == 'openhands'
    assert kwargs['params']['consumerInputs']['basicAuthPassword'] == 'secret'


@pytest.mark.asyncio
async def test_installations_ignore_stored_organization():
    service = SaaSAzureDevOpsService(
        token=SecretStr('token'), base_domain='configured-org'
    )
    with patch.object(service, '_make_request', new_callable=AsyncMock) as request:
        request.side_effect = [
            ({'id': 'member-id'}, {}),
            (
                {
                    'value': [
                        {'accountName': 'Alpha'},
                        {'accountName': 'Beta'},
                        {'accountId': 'no-name'},
                    ]
                },
                {},
            ),
        ]
        assert await service.get_installations() == ['Alpha', 'Beta']
        assert 'memberId=member-id' in request.await_args.args[0]


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.expiry: dict[str, int] = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value
        self.expiry[key] = ex


class _FakeAzure:
    """Azure DevOps APIs for one member across several organizations."""

    def __init__(
        self, repos_by_org: dict[str, list[str]], pr_by_org=None, error_orgs=None
    ):
        self.repos_by_org = repos_by_org
        self.pr_by_org = pr_by_org or {}
        # Organizations the membership lookup still reports (a membership-cache
        # window for a just-removed member) but whose API calls reject.
        self.error_orgs = set(error_orgs or [])
        # Azure answers a throttled profile request with 500 "Too Many Requests".
        self.throttled = False
        # Organizations whose API calls reject the token (401).
        self.unauthorized_orgs: set[str] = set()
        # A revoked token: every Azure call returns 401.
        self.revoked = False
        self.requests: list[httpx.Request] = []

    def _repo(self, org, name):
        return {'id': f'{org}-{name}', 'name': name, 'project': {'name': 'Proj'}}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if self.revoked:
            return httpx.Response(401, json={'message': 'Unauthorized'})
        if request.url.host == 'app.vssps.visualstudio.com':
            if path == '/_apis/profile/profiles/me':
                if self.throttled:
                    return httpx.Response(
                        500, json={'message': 'VssServiceException: Too Many Requests'}
                    )
                return httpx.Response(200, json={'id': 'member-1'})
            if path == '/_apis/accounts':
                names = list(self.repos_by_org)
                value = [{'accountName': name} for name in names]
                return httpx.Response(200, json={'value': value})
        org, _, rest = path.lstrip('/').partition('/')
        if org not in self.repos_by_org:
            return httpx.Response(404, json={'message': 'not a member'})
        if org in self.error_orgs:
            return httpx.Response(403, json={'message': 'access denied'})
        if org in self.unauthorized_orgs:
            return httpx.Response(401, json={'message': 'Unauthorized'})
        repos = [self._repo(org, name) for name in self.repos_by_org[org]]
        if rest == '_apis/projects':
            return httpx.Response(200, json={'value': [{'name': 'Proj'}]})
        if rest in ('_apis/git/repositories', 'Proj/_apis/git/repositories'):
            return httpx.Response(200, json={'value': repos})
        if rest == '_apis/connectionData':
            return httpx.Response(200, json={'authenticatedUser': {'id': 'member-1'}})
        if rest == 'Proj/_apis/git/pullrequests':
            prs = (
                [
                    {
                        'pullRequestId': 1,
                        'title': self.pr_by_org[org],
                        'mergeStatus': 'conflicts',
                        'repository': {'name': repos[0]['name']},
                    }
                ]
                if org in self.pr_by_org
                else []
            )
            return httpx.Response(200, json={'value': prs})
        if rest == 'Proj/_apis/wit/wiql':
            return httpx.Response(200, json={'workItems': []})
        raise AssertionError(f'unexpected Azure request {request.url}')

    def orgs_requested(self) -> set[str]:
        return {
            r.url.path.lstrip('/').split('/')[0]
            for r in self.requests
            if r.url.host == 'dev.azure.com'
        }


@pytest.fixture
def fake_redis(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(
        'integrations.azure_devops.azure_devops_service.get_redis_client_async',
        lambda: redis,
    )
    return redis


@pytest.fixture
def azure(monkeypatch, fake_redis):
    fake = _FakeAzure({'Alpha': ['one', 'two'], 'Beta': ['only-in-beta']})

    # A class, not a lambda, so annotations such as `httpx.AsyncClient | None`
    # in modules imported during the test still evaluate.
    class MockClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs['transport'] = httpx.MockTransport(fake)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', MockClient)
    return fake


@pytest.fixture
def no_token_refresh(monkeypatch):
    # A 401 triggers one token refresh before the request fails.
    monkeypatch.setattr(
        SaaSAzureDevOpsService, 'get_latest_token', AsyncMock(return_value=None)
    )


def _user_service(base_domain=None) -> SaaSAzureDevOpsService:
    return SaaSAzureDevOpsService(
        external_auth_id='kc-user', token=SecretStr('token'), base_domain=base_domain
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('stored_host', [None, 'Alpha'])
async def test_installations_are_every_member_organization(
    azure, monkeypatch, stored_host
):
    # Neither the former install-wide default nor a stored per-user host
    # narrows the organizations.
    monkeypatch.setenv('AZURE_DEVOPS_ORGANIZATION', 'Alpha')
    assert await _user_service(stored_host).get_installations() == ['Alpha', 'Beta']


@pytest.mark.asyncio
async def test_installations_for_single_organization_member(azure):
    azure.repos_by_org = {'Alpha': ['one']}
    assert await _user_service().get_installations() == ['Alpha']


@pytest.mark.asyncio
async def test_membership_is_cached_per_user_for_a_short_time(azure, fake_redis):
    assert await _user_service().get_installations() == ['Alpha', 'Beta']
    azure.requests.clear()

    assert await _user_service().get_installations() == ['Alpha', 'Beta']

    assert azure.requests == []
    assert fake_redis.expiry == {
        'azure_devops:organizations:kc-user': 60,
        'azure_devops:organizations:kc-user:last-known': 24 * 60 * 60,
    }


@pytest.mark.asyncio
async def test_discovery_failure_uses_last_known_memberships(azure, fake_redis):
    assert await _user_service().get_installations() == ['Alpha', 'Beta']
    # The short cache expires, then Azure throttles discovery.
    del fake_redis.values['azure_devops:organizations:kc-user']
    azure.throttled = True

    assert await _user_service().get_installations() == ['Alpha', 'Beta']


@pytest.mark.asyncio
async def test_discovery_failure_without_last_known_memberships_raises(azure):
    azure.throttled = True

    with pytest.raises(UnknownException):
        await _user_service().get_installations()


@pytest.mark.asyncio
async def test_rejected_token_is_raised_instead_of_last_known_memberships(
    azure, fake_redis, no_token_refresh
):
    assert await _user_service().get_installations() == ['Alpha', 'Beta']
    del fake_redis.values['azure_devops:organizations:kc-user']
    azure.revoked = True

    with pytest.raises(AuthenticationError):
        await _user_service().get_installations()


@pytest.mark.asyncio
async def test_repositories_for_installation_use_canonical_organization(azure):
    repos = await _user_service().get_paginated_repos(
        page=1, per_page=10, sort='pushed', installation_id='beta'
    )

    assert [r.full_name for r in repos] == ['Beta/Proj/only-in-beta']
    assert azure.orgs_requested() == {'Beta'}


@pytest.mark.asyncio
async def test_repositories_for_non_member_installation_are_forbidden(azure):
    with pytest.raises(HTTPException) as exc_info:
        await _user_service().get_paginated_repos(
            page=1, per_page=10, sort='pushed', installation_id='Gamma'
        )

    assert exc_info.value.status_code == 403
    assert azure.orgs_requested() == set()


@pytest.mark.asyncio
async def test_repositories_without_installation_span_every_organization(azure):
    repos = await _user_service().get_paginated_repos(
        page=1, per_page=10, sort='name', installation_id=None
    )

    assert sorted(r.full_name for r in repos) == [
        'Alpha/Proj/one',
        'Alpha/Proj/two',
        'Beta/Proj/only-in-beta',
    ]


@pytest.mark.asyncio
async def test_installation_pages_return_each_repository_once(azure):
    azure.repos_by_org = {'Alpha': ['r1', 'r2', 'r3', 'r4', 'r5']}
    service = _user_service()

    pages = [
        await service.get_paginated_repos(
            page=page, per_page=2, sort='name', installation_id='Alpha'
        )
        for page in (1, 2, 3, 4)
    ]

    names = [r.full_name.rsplit('/', 1)[1] for page in pages for r in page]
    assert names == ['r1', 'r2', 'r3', 'r4', 'r5']


@pytest.mark.asyncio
async def test_search_finds_repositories_in_any_organization(azure):
    repos = await _user_service().search_repositories('beta', per_page=5)

    assert [r.full_name for r in repos] == ['Beta/Proj/only-in-beta']
    assert all(org in azure.orgs_requested() for org in ('Alpha', 'Beta'))


@pytest.mark.asyncio
async def test_search_merges_organizations_and_caps_results(azure):
    azure.repos_by_org = {'Alpha': ['app', 'api'], 'Beta': ['apps']}

    repos = await _user_service().search_repositories('ap', per_page=2)

    assert len(repos) == 2
    assert len({r.full_name for r in repos}) == 2


@pytest.mark.asyncio
async def test_suggested_tasks_cover_every_organization(azure):
    azure.pr_by_org = {'Alpha': 'Alpha PR', 'Beta': 'Beta PR'}

    tasks = await _user_service().get_suggested_tasks()

    assert sorted((t.repo, t.title) for t in tasks) == [
        ('Alpha/Proj/one', 'Alpha PR'),
        ('Beta/Proj/only-in-beta', 'Beta PR'),
    ]


@pytest.mark.asyncio
async def test_cross_organization_listing_skips_failing_organizations(azure):
    # A just-removed member still has the org in the membership cache for up to
    # a minute; the org's API rejects with 403. Listing must return the healthy
    # orgs rather than fail the whole request.
    azure.error_orgs = {'Beta'}

    repos = await _user_service().get_paginated_repos(
        page=1, per_page=10, sort='name', installation_id=None
    )

    assert sorted(r.full_name for r in repos) == [
        'Alpha/Proj/one',
        'Alpha/Proj/two',
    ]
    assert azure.orgs_requested() == {'Alpha', 'Beta'}


@pytest.mark.asyncio
async def test_cross_organization_search_skips_failing_organizations(azure):
    azure.error_orgs = {'Beta'}

    repos = await _user_service().search_repositories('one', per_page=5)

    assert [r.full_name for r in repos] == ['Alpha/Proj/one']


@pytest.mark.asyncio
async def test_cross_organization_suggested_tasks_skip_failing_organizations(azure):
    azure.error_orgs = {'Beta'}
    azure.pr_by_org = {'Alpha': 'Alpha PR'}

    tasks = await _user_service().get_suggested_tasks()

    assert [(t.repo, t.title) for t in tasks] == [('Alpha/Proj/one', 'Alpha PR')]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'operation',
    [
        lambda service: service.get_paginated_repos(
            page=1, per_page=10, sort='name', installation_id=None
        ),
        lambda service: service.search_repositories('one', per_page=5),
        lambda service: service.get_suggested_tasks(),
    ],
    ids=['listing', 'search', 'suggested-tasks'],
)
async def test_every_organization_rejecting_the_token_raises(
    azure, no_token_refresh, operation
):
    azure.unauthorized_orgs = {'Alpha', 'Beta'}

    with pytest.raises(AuthenticationError):
        await operation(_user_service())


@pytest.mark.asyncio
async def test_one_organization_rejecting_the_token_is_skipped(azure, no_token_refresh):
    azure.unauthorized_orgs = {'Beta'}

    repos = await _user_service().get_paginated_repos(
        page=1, per_page=10, sort='name', installation_id=None
    )

    assert sorted(r.full_name for r in repos) == [
        'Alpha/Proj/one',
        'Alpha/Proj/two',
    ]
