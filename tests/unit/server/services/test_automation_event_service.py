"""
Unit tests for AutomationEventService.

Tests the service that forwards GitHub webhook events to the automation service.

The service is optimized for high-traffic with:
- Redis caching for org claim lookups (1 hour TTL)
- Redis caching for provider→Keycloak user ID mappings (24 hour TTL)
- Lazy access control (membership checks deferred to execution time)
- Separate AUTOMATION_WEBHOOK_SECRET for internal service communication
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openhands.app_server.integrations.service_types import ProviderType

REDIS_PATCH = 'server.services.automation_event_service.get_redis_client_async'

# Default patches for constants
CONSTANT_PATCHES = {
    'server.services.automation_event_service.AUTOMATION_WEBHOOK_SECRET': 'test-shared-secret',
    'server.services.automation_event_service.AUTOMATION_SERVICE_TIMEOUT': 30,
}


@pytest.fixture
def mock_token_manager():
    """Create a mock TokenManager."""
    return MagicMock()


@pytest.fixture
def mock_org_git_claim():
    """Create a mock OrgGitClaim."""
    claim = MagicMock()
    claim.org_id = uuid.UUID('12345678-1234-5678-1234-567812345678')
    return claim


@pytest.fixture
def github_org_payload():
    """Create a sample GitHub webhook payload for an organization repo."""
    return {
        'repository': {
            'id': 123456,
            'full_name': 'test-org/test-repo',
            'private': False,
            'default_branch': 'main',
            'owner': {
                'login': 'test-org',
                'id': 789,
                'type': 'Organization',
            },
        },
        'sender': {
            'id': 12345,
            'login': 'testuser',
        },
        'action': 'opened',
        'installation': {
            'id': 99999,
        },
    }


@pytest.fixture
def github_user_payload():
    """Create a sample GitHub webhook payload for a personal/user repo."""
    return {
        'repository': {
            'id': 654321,
            'full_name': 'testuser/personal-repo',
            'private': True,
            'default_branch': 'main',
            'owner': {
                'login': 'testuser',
                'id': 12345,
                'type': 'User',
            },
        },
        'sender': {
            'id': 12345,
            'login': 'testuser',
        },
        'action': 'opened',
        'installation': {
            'id': 99999,
        },
    }


@pytest.fixture
def bitbucket_dc_pr_payload():
    """Create a sample Bitbucket DC PR webhook payload."""
    return {
        'eventKey': 'pr:opened',
        'pullRequest': {
            'id': 1,
            'toRef': {
                'repository': {
                    'slug': 'myrepo',
                    'project': {'key': 'PROJ'},
                }
            },
        },
        'actor': {'name': 'testuser'},
    }


@pytest.fixture
def bitbucket_dc_repo_payload():
    """Create a sample Bitbucket DC repo-level webhook payload."""
    return {
        'eventKey': 'repo:refs_changed',
        'repository': {
            'slug': 'myrepo',
            'project': {'key': 'PROJ'},
        },
        'changes': [{'refId': 'refs/heads/main'}],
    }


@pytest.fixture
def azure_devops_pr_payload():
    """Create a sample Azure DevOps pull request Service Hook payload."""
    return {
        'id': 'evt-1',
        'subscriptionId': 'sub-1',
        'eventType': 'git.pullrequest.created',
        'resource': {
            'pullRequestId': 42,
            'repository': {
                'name': 'fabrikam-web',
                'project': {'name': 'Fabrikam Fiber'},
            },
            'url': (
                'https://dev.azure.com/Fabrikam/proj-id/_apis/git/repositories/'
                'repo-id/pullRequests/42'
            ),
        },
        'resourceContainers': {
            'account': {'baseUrl': 'https://dev.azure.com/Fabrikam/'},
        },
    }


def create_service(mock_token_manager):
    """Helper to create a service with mocked constants."""
    with patch.dict('os.environ', {}, clear=False):
        for key, value in CONSTANT_PATCHES.items():
            patch(key, value).start()

        from server.services.automation_event_service import AutomationEventService

        return AutomationEventService(mock_token_manager)


class TestResolveGitOrg:
    """Tests for _resolve_git_org method with caching."""

    @pytest.mark.asyncio
    async def test_resolve_git_org_cache_miss_found(
        self, mock_token_manager, mock_org_git_claim
    ):
        """
        GIVEN: Cache miss and org claim exists in DB
        WHEN: _resolve_git_org is called
        THEN: Org ID is returned and cached
        """
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # Cache miss
        mock_redis.setex = AsyncMock()

        with (
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=mock_org_git_claim.org_id,
            ),
            patch(REDIS_PATCH, return_value=mock_redis),
        ):
            service = create_service(mock_token_manager)
            result = await service._resolve_git_org(ProviderType.GITHUB, 'test-org')

            assert result == mock_org_git_claim.org_id
            # Verify result was cached
            mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_git_org_cache_hit(self, mock_token_manager):
        """
        GIVEN: Org ID is cached in Redis
        WHEN: _resolve_git_org is called
        THEN: Cached value is returned without calling resolve_org_for_repo
        """
        cached_org_id = '12345678-1234-5678-1234-567812345678'
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=cached_org_id.encode())

        with (
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
            ) as mock_resolver,
            patch(REDIS_PATCH, return_value=mock_redis),
        ):
            service = create_service(mock_token_manager)
            result = await service._resolve_git_org(ProviderType.GITHUB, 'test-org')

            assert result == uuid.UUID(cached_org_id)
            # resolve_org_for_repo should NOT be called
            mock_resolver.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_git_org_cache_miss_not_found(self, mock_token_manager):
        """
        GIVEN: Cache miss and org claim does NOT exist in DB
        WHEN: _resolve_git_org is called
        THEN: None is returned and negative result is cached
        """
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # Cache miss
        mock_redis.setex = AsyncMock()

        with (
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(REDIS_PATCH, return_value=mock_redis),
        ):
            service = create_service(mock_token_manager)
            result = await service._resolve_git_org(
                ProviderType.GITHUB, 'unclaimed-org'
            )

            assert result is None
            # Verify negative result was cached
            mock_redis.setex.assert_called_once()
            call_args = mock_redis.setex.call_args
            # Second positional arg is the value
            assert call_args[0][2] == 'none'  # Negative cache value

    @pytest.mark.asyncio
    async def test_resolve_git_org_negative_cache_hit(self, mock_token_manager):
        """
        GIVEN: Negative result is cached (org not claimed)
        WHEN: _resolve_git_org is called
        THEN: None is returned without calling resolve_org_for_repo
        """
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=b'none')  # Cached negative

        with (
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
            ) as mock_resolver,
            patch(REDIS_PATCH, return_value=mock_redis),
        ):
            service = create_service(mock_token_manager)
            result = await service._resolve_git_org(
                ProviderType.GITHUB, 'unclaimed-org'
            )

            assert result is None
            mock_resolver.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_git_org_includes_provider_in_cache_key(
        self, mock_token_manager, mock_org_git_claim
    ):
        """
        GIVEN: GitHub provider with an org name
        WHEN: _resolve_git_org is called
        THEN: Cache key includes the provider name
        """
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with (
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=mock_org_git_claim.org_id,
            ),
            patch(REDIS_PATCH, return_value=mock_redis),
        ):
            service = create_service(mock_token_manager)

            # Call for GitHub
            await service._resolve_git_org(ProviderType.GITHUB, 'test-org')
            github_cache_key = mock_redis.setex.call_args_list[0][0][0]

            # Cache key should include provider
            assert 'github' in github_cache_key


class TestResolvePersonalOrg:
    """Tests for _resolve_personal_org method with caching."""

    @pytest.mark.asyncio
    async def test_resolve_personal_org_cache_miss_found(self, mock_token_manager):
        """
        GIVEN: Cache miss and user exists in Keycloak
        WHEN: _resolve_personal_org is called
        THEN: Keycloak ID is returned and cached
        """
        keycloak_id = '87654321-4321-8765-4321-876543218765'
        mock_token_manager.get_user_id_from_idp_user_id = AsyncMock(
            return_value=keycloak_id
        )

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # Cache miss
        mock_redis.setex = AsyncMock()

        with patch(REDIS_PATCH, return_value=mock_redis):
            service = create_service(mock_token_manager)
            result = await service._resolve_personal_org(ProviderType.GITHUB, 12345)

            assert result == uuid.UUID(keycloak_id)
            mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_personal_org_cache_hit(self, mock_token_manager):
        """
        GIVEN: Keycloak ID is cached in Redis
        WHEN: _resolve_personal_org is called
        THEN: Cached value is returned without Keycloak query
        """
        keycloak_id = '87654321-4321-8765-4321-876543218765'
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=keycloak_id.encode())

        with patch(REDIS_PATCH, return_value=mock_redis):
            service = create_service(mock_token_manager)
            result = await service._resolve_personal_org(ProviderType.GITHUB, 12345)

            assert result == uuid.UUID(keycloak_id)
            # Token manager should NOT be called
            mock_token_manager.get_user_id_from_idp_user_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_personal_org_no_user_id(self, mock_token_manager):
        """
        GIVEN: No provider user ID provided
        WHEN: _resolve_personal_org is called
        THEN: None is returned immediately
        """
        service = create_service(mock_token_manager)
        result = await service._resolve_personal_org(ProviderType.GITHUB, None)

        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_personal_org_includes_provider_in_cache_key(
        self, mock_token_manager
    ):
        """
        GIVEN: GitHub provider with a user ID
        WHEN: _resolve_personal_org is called
        THEN: Cache key includes the provider name
        """
        keycloak_id = '87654321-4321-8765-4321-876543218765'
        mock_token_manager.get_user_id_from_idp_user_id = AsyncMock(
            return_value=keycloak_id
        )

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with patch(REDIS_PATCH, return_value=mock_redis):
            service = create_service(mock_token_manager)

            # Call for GitHub
            await service._resolve_personal_org(ProviderType.GITHUB, 12345)
            github_cache_key = mock_redis.setex.call_args_list[0][0][0]

            # Cache key should include provider
            assert 'github' in github_cache_key


class _DictRedis:
    """In-memory stand-in for the async Redis client; unit tests have no Redis."""

    def __init__(self):
        self.data: dict[str, str] = {}

    async def get(self, key):
        return self.data.get(key)

    async def setex(self, key, ttl, value):
        self.data[key] = value

    async def delete(self, *keys):
        return sum(self.data.pop(key, None) is not None for key in keys)


class TestUserIdCacheInvalidation:
    """A link or unlink must not leave a stale provider→user mapping cached."""

    @pytest.mark.asyncio
    async def test_a_cached_miss_is_dropped_when_the_account_is_linked(
        self, mock_token_manager
    ):
        from server.services.automation_event_service import (
            invalidate_user_id_cache,
        )

        user_id = '87654321-4321-8765-4321-876543218765'
        mock_token_manager.get_user_id_from_idp_user_id = AsyncMock(
            side_effect=[None, user_id]
        )
        redis = _DictRedis()

        with patch(REDIS_PATCH, return_value=redis):
            service = create_service(mock_token_manager)
            assert (
                await service._resolve_personal_org(ProviderType.GITHUB, 12345) is None
            )

            # Webhooks pass the id as an int; the link stores it as text.
            await invalidate_user_id_cache(ProviderType.GITHUB, '12345')

            result = await service._resolve_personal_org(ProviderType.GITHUB, 12345)

        assert result == uuid.UUID(user_id)

    @pytest.mark.asyncio
    async def test_a_cached_user_is_dropped_when_the_account_is_unlinked(
        self, mock_token_manager
    ):
        from server.services.automation_event_service import (
            invalidate_user_id_cache,
        )

        mock_token_manager.get_user_id_from_idp_user_id = AsyncMock(
            side_effect=['87654321-4321-8765-4321-876543218765', None]
        )
        redis = _DictRedis()

        with patch(REDIS_PATCH, return_value=redis):
            service = create_service(mock_token_manager)
            assert await service._resolve_personal_org(ProviderType.GITHUB, 12345)

            await invalidate_user_id_cache(ProviderType.GITHUB, '12345')

            result = await service._resolve_personal_org(ProviderType.GITHUB, 12345)

        assert result is None

    @pytest.mark.asyncio
    async def test_invalidation_does_not_raise_when_redis_is_unavailable(self):
        from server.services.automation_event_service import (
            invalidate_user_id_cache,
        )

        redis = AsyncMock()
        redis.delete = AsyncMock(side_effect=ConnectionError('redis down'))

        with patch(REDIS_PATCH, return_value=redis):
            await invalidate_user_id_cache(ProviderType.GITHUB, '12345')

        redis.delete.assert_awaited_once()


class TestOrgClaimCacheInvalidation:
    """Creating or removing a git claim must not leave a stale lookup cached."""

    @pytest.fixture
    def claims_db(self, async_session_maker, monkeypatch):
        """Claims live in the test database; no default-org fallback applies."""
        monkeypatch.delenv('OPENHANDS_DEFAULT_ORG_ENABLED', raising=False)
        monkeypatch.delenv('OH_DEFAULT_ORG_ENABLED', raising=False)
        with patch('storage.org_git_claim_store.a_session_maker', async_session_maker):
            yield

    @pytest.fixture
    def org_admin(self, create_org, create_user):
        """Create an org and a user, returned as (org_id, user_id)."""

        def _create() -> tuple[uuid.UUID, str]:
            org = create_org()
            user = create_user(current_org_id=org.id)
            return org.id, str(user.id)

        return _create

    @pytest.mark.asyncio
    async def test_a_claim_made_after_a_dropped_event_routes_the_next_event(
        self, mock_token_manager, bitbucket_dc_pr_payload, claims_db, org_admin
    ):
        """
        GIVEN: An event for an unclaimed Bitbucket DC project was dropped and
               the miss cached for ORG_CLAIM_CACHE_TTL_SECONDS
        WHEN: An org admin claims the project
        THEN: The next event routes to that org without waiting for the TTL
        """
        from server.routes.org_models import GitOrgClaimRequest
        from server.routes.orgs import claim_git_organization

        org_id, user_id = org_admin()
        redis = _DictRedis()

        with patch(REDIS_PATCH, return_value=redis):
            service = create_service(mock_token_manager)
            first = await service._resolve_org_context(
                ProviderType.BITBUCKET_DATA_CENTER, bitbucket_dc_pr_payload
            )
            assert first is None
            assert redis.data == {
                'automation:org_claim:bitbucket_data_center:proj': 'none'
            }

            await claim_git_organization(
                org_id=org_id,
                request=GitOrgClaimRequest(
                    provider='bitbucket_data_center', git_organization='PROJ'
                ),
                user_id=user_id,
            )
            second = await service._resolve_org_context(
                ProviderType.BITBUCKET_DATA_CENTER, bitbucket_dc_pr_payload
            )

        assert second is not None
        assert second.org_id == org_id
        assert second.git_org == 'PROJ'

    @pytest.mark.asyncio
    async def test_removing_a_claim_stops_routing_to_its_org(
        self, mock_token_manager, github_org_payload, claims_db, org_admin
    ):
        """
        GIVEN: Events for a claimed GitHub org cached the claiming org's id
        WHEN: The claim is removed
        THEN: The next event is no longer routed to that org
        """
        from server.routes.orgs import disconnect_git_organization
        from storage.org_git_claim_store import OrgGitClaimStore

        org_id, user_id = org_admin()
        claim = await OrgGitClaimStore.create_claim(
            org_id=org_id,
            provider='github',
            git_organization='test-org',
            claimed_by=uuid.UUID(user_id),
        )
        redis = _DictRedis()

        with patch(REDIS_PATCH, return_value=redis):
            service = create_service(mock_token_manager)
            first = await service._resolve_org_context(
                ProviderType.GITHUB, github_org_payload
            )
            assert first is not None and first.org_id == org_id
            assert redis.data == {'automation:org_claim:github:test-org': str(org_id)}

            await disconnect_git_organization(
                org_id=org_id, claim_id=claim.id, user_id=user_id
            )
            second = await service._resolve_org_context(
                ProviderType.GITHUB, github_org_payload
            )

        assert second is None

    @pytest.mark.asyncio
    async def test_a_claim_moved_to_another_org_routes_there(
        self, mock_token_manager, azure_devops_pr_payload, claims_db, org_admin
    ):
        """
        GIVEN: An Azure DevOps org claimed by one OpenHands org, cached
        WHEN: That claim is removed, an event arrives, and another org claims it
        THEN: The event in between is dropped and the next one routes to the
              new org
        """
        from server.routes.org_models import GitOrgClaimRequest
        from server.routes.orgs import (
            claim_git_organization,
            disconnect_git_organization,
        )
        from storage.org_git_claim_store import OrgGitClaimStore

        old_org_id, old_admin_id = org_admin()
        new_org_id, new_admin_id = org_admin()
        claim = await OrgGitClaimStore.create_claim(
            org_id=old_org_id,
            provider='azure_devops',
            git_organization='fabrikam',
            claimed_by=uuid.UUID(old_admin_id),
        )
        redis = _DictRedis()

        with patch(REDIS_PATCH, return_value=redis):
            service = create_service(mock_token_manager)
            before = await service._resolve_org_context(
                ProviderType.AZURE_DEVOPS, azure_devops_pr_payload
            )
            assert before is not None and before.org_id == old_org_id

            await disconnect_git_organization(
                org_id=old_org_id, claim_id=claim.id, user_id=old_admin_id
            )
            between = await service._resolve_org_context(
                ProviderType.AZURE_DEVOPS, azure_devops_pr_payload
            )
            assert between is None

            await claim_git_organization(
                org_id=new_org_id,
                request=GitOrgClaimRequest(
                    provider='azure_devops', git_organization=' Fabrikam '
                ),
                user_id=new_admin_id,
            )
            after = await service._resolve_org_context(
                ProviderType.AZURE_DEVOPS, azure_devops_pr_payload
            )

        assert after is not None
        assert after.org_id == new_org_id

    @pytest.mark.asyncio
    async def test_invalidation_clears_the_key_the_lookup_uses(
        self, mock_token_manager
    ):
        """
        GIVEN: A lookup for "Test-Org" cached its org, and another org is cached
        WHEN: The claim stored as "test-org" is invalidated
        THEN: Only that lookup is cleared
        """
        from server.services.automation_event_service import (
            invalidate_org_claim_cache,
        )

        org_id = uuid.UUID('12345678-1234-5678-1234-567812345678')
        redis = _DictRedis()
        redis.data['automation:org_claim:github:other-org'] = 'none'

        with (
            patch(REDIS_PATCH, return_value=redis),
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=org_id,
            ),
        ):
            service = create_service(mock_token_manager)
            assert await service._resolve_git_org(ProviderType.GITHUB, 'Test-Org')
            await invalidate_org_claim_cache('github', 'test-org')

        assert redis.data == {'automation:org_claim:github:other-org': 'none'}

    @pytest.mark.asyncio
    async def test_lookup_without_a_cached_entry_is_unchanged(self, mock_token_manager):
        """
        GIVEN: Nothing cached for a Git org
        WHEN: An event looks it up
        THEN: The claim query and cache entry are the ones routing always used
        """
        org_id = uuid.UUID('12345678-1234-5678-1234-567812345678')
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with (
            patch(REDIS_PATCH, return_value=mock_redis),
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=org_id,
            ) as mock_resolver,
        ):
            service = create_service(mock_token_manager)
            result = await service._resolve_git_org(ProviderType.GITHUB, 'Test-Org')

        assert result == org_id
        mock_resolver.assert_awaited_once_with(
            provider='github', full_repo_name='test-org/'
        )
        mock_redis.get.assert_awaited_once_with('automation:org_claim:github:test-org')
        mock_redis.setex.assert_awaited_once_with(
            'automation:org_claim:github:test-org', 3600, str(org_id)
        )

    @pytest.mark.asyncio
    async def test_invalidation_logs_and_does_not_raise_when_redis_fails(self):
        from server.services.automation_event_service import (
            invalidate_org_claim_cache,
        )

        redis = AsyncMock()
        redis.delete = AsyncMock(side_effect=ConnectionError('redis down'))

        with (
            patch(REDIS_PATCH, return_value=redis),
            patch('server.services.automation_event_service.logger') as logger,
        ):
            await invalidate_org_claim_cache('bitbucket_data_center', 'proj')

        redis.delete.assert_awaited_once_with(
            'automation:org_claim:bitbucket_data_center:proj'
        )
        logger.warning.assert_called_once()


class TestForwardEvent:
    """Tests for forward_event method (minimal payload, no access control)."""

    @pytest.mark.asyncio
    async def test_forward_org_event_success(
        self, mock_token_manager, github_org_payload, mock_org_git_claim
    ):
        """
        GIVEN: A GitHub event from a claimed organization repo
        WHEN: forward_event is called
        THEN: Minimal payload is forwarded (no access_control)
        """
        from server.services.automation_event_service import AutomationEventService

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with (
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=mock_org_git_claim.org_id,
            ),
            patch(REDIS_PATCH, return_value=mock_redis),
            patch.object(
                AutomationEventService,
                '_send_to_automation_service',
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            service = AutomationEventService(mock_token_manager)
            await service.forward_event(
                provider=ProviderType.GITHUB,
                payload=github_org_payload,
                installation_id=99999,
            )

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            # Provider is first arg, org_id is second
            assert call_args[0][0] == ProviderType.GITHUB
            assert call_args[0][1] == mock_org_git_claim.org_id

            payload = call_args[0][2]
            assert payload['organization']['git_org'] == 'test-org'
            assert 'payload' in payload
            # access_control should NOT be in payload (lazy evaluation)
            assert 'access_control' not in payload

    @pytest.mark.asyncio
    async def test_forward_personal_repo_event_success(
        self, mock_token_manager, github_user_payload
    ):
        """
        GIVEN: A GitHub event from a personal repo with linked OpenHands account
        WHEN: forward_event is called
        THEN: Event is forwarded using the user's personal org (keycloak ID)
        """
        from server.services.automation_event_service import AutomationEventService

        keycloak_id = '87654321-4321-8765-4321-876543218765'
        mock_token_manager.get_user_id_from_idp_user_id = AsyncMock(
            return_value=keycloak_id
        )

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with (
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=None,  # No org claim for personal repo
            ),
            patch(REDIS_PATCH, return_value=mock_redis),
            patch.object(
                AutomationEventService,
                '_send_to_automation_service',
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            service = AutomationEventService(mock_token_manager)
            await service.forward_event(
                provider=ProviderType.GITHUB,
                payload=github_user_payload,
                installation_id=99999,
            )

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            # Provider is first arg, org_id is second (personal org = keycloak ID)
            assert call_args[0][0] == ProviderType.GITHUB
            assert call_args[0][1] == uuid.UUID(keycloak_id)
            payload = call_args[0][2]
            assert payload['organization']['git_org'] == 'testuser'
            assert payload['organization']['openhands_org_id'] == keycloak_id

    @staticmethod
    def _requested_metadata(event_types: set[str]):
        from server.services.automation_event_service import (
            EventDetectionRule,
            RequestedEventTypesCacheEntry,
        )

        return RequestedEventTypesCacheEntry(
            event_types=event_types,
            event_detection_rules=[
                EventDetectionRule(
                    event_type='pull_request',
                    jmespath="contains(keys(@), 'pull_request')",
                ),
                EventDetectionRule(
                    event_type='push',
                    jmespath="contains(keys(@), 'ref') && contains(keys(@), 'commits')",
                ),
            ],
            loaded_at=0,
        )

    @pytest.mark.asyncio
    async def test_forward_event_skips_unrequested_github_event(
        self, mock_token_manager, github_org_payload
    ):
        from server.services.automation_event_service import AutomationEventService

        payload = {**github_org_payload, 'pull_request': {'number': 1}}

        with (
            patch.object(
                AutomationEventService,
                '_get_requested_event_metadata',
                new_callable=AsyncMock,
                return_value=self._requested_metadata({'push'}),
            ),
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
            ) as mock_resolver,
            patch.object(
                AutomationEventService,
                '_send_to_automation_service',
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            service = AutomationEventService(mock_token_manager)
            await service.forward_event(
                provider=ProviderType.GITHUB,
                payload=payload,
                installation_id=99999,
                event_type='pull_request',
            )

            mock_resolver.assert_not_called()
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_forward_event_allows_requested_github_event(
        self, mock_token_manager, github_org_payload, mock_org_git_claim
    ):
        from server.services.automation_event_service import AutomationEventService

        payload = {**github_org_payload, 'pull_request': {'number': 1}}
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with (
            patch.object(
                AutomationEventService,
                '_get_requested_event_metadata',
                new_callable=AsyncMock,
                return_value=self._requested_metadata({'pull_request'}),
            ),
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=mock_org_git_claim.org_id,
            ),
            patch(REDIS_PATCH, return_value=mock_redis),
            patch.object(
                AutomationEventService,
                '_send_to_automation_service',
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            service = AutomationEventService(mock_token_manager)
            await service.forward_event(
                provider=ProviderType.GITHUB,
                payload=payload,
                installation_id=99999,
                event_type='pull_request',
            )

            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_forward_event_uses_detection_rules_not_github_header(
        self, mock_token_manager, github_org_payload, mock_org_git_claim
    ):
        from server.services.automation_event_service import AutomationEventService

        payload = {**github_org_payload, 'pull_request': {'number': 1}}
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with (
            patch.object(
                AutomationEventService,
                '_get_requested_event_metadata',
                new_callable=AsyncMock,
                return_value=self._requested_metadata({'pull_request'}),
            ),
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=mock_org_git_claim.org_id,
            ),
            patch(REDIS_PATCH, return_value=mock_redis),
            patch.object(
                AutomationEventService,
                '_send_to_automation_service',
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            service = AutomationEventService(mock_token_manager)
            await service.forward_event(
                provider=ProviderType.GITHUB,
                payload=payload,
                installation_id=99999,
                event_type='pull_request_review_comment',
            )

            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_requested_event_metadata_returns_none_without_server_truth(
        self, mock_token_manager
    ):
        from server.services.automation_event_service import AutomationEventService

        with patch.object(
            AutomationEventService,
            '_fetch_requested_event_metadata',
            new_callable=AsyncMock,
            return_value=None,
        ):
            service = AutomationEventService(mock_token_manager)
            metadata = await service._get_requested_event_metadata('github')

        assert metadata is None

    @pytest.mark.asyncio
    async def test_forward_event_allows_when_requested_types_unavailable(
        self, mock_token_manager, github_org_payload, mock_org_git_claim
    ):
        from server.services.automation_event_service import AutomationEventService

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with (
            patch.object(
                AutomationEventService,
                '_get_requested_event_metadata',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=mock_org_git_claim.org_id,
            ),
            patch(REDIS_PATCH, return_value=mock_redis),
            patch.object(
                AutomationEventService,
                '_send_to_automation_service',
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            service = AutomationEventService(mock_token_manager)
            await service.forward_event(
                provider=ProviderType.GITHUB,
                payload=github_org_payload,
                installation_id=99999,
                event_type='workflow_job',
            )

            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_forward_event_no_owner_in_payload(self, mock_token_manager):
        """
        GIVEN: A GitHub event with no repository owner in payload
        WHEN: forward_event is called
        THEN: Event is skipped with warning log
        """
        from server.services.automation_event_service import AutomationEventService

        payload = {
            'repository': {},
            'sender': {'id': 12345, 'login': 'testuser'},
        }

        with (
            patch('server.services.automation_event_service.logger') as mock_logger,
            patch.object(
                AutomationEventService,
                '_send_to_automation_service',
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            service = AutomationEventService(mock_token_manager)
            await service.forward_event(
                provider=ProviderType.GITHUB,
                payload=payload,
                installation_id=99999,
            )

            mock_send.assert_not_called()
            mock_logger.warning.assert_called()
            assert 'No repository owner' in str(mock_logger.warning.call_args)

    @pytest.mark.asyncio
    async def test_forward_event_org_not_claimed_and_not_personal(
        self, mock_token_manager, github_org_payload
    ):
        """
        GIVEN: A GitHub event from an org that isn't claimed (and isn't personal)
        WHEN: forward_event is called
        THEN: Event is skipped with warning log
        """
        from server.services.automation_event_service import AutomationEventService

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with (
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(REDIS_PATCH, return_value=mock_redis),
            patch('server.services.automation_event_service.logger') as mock_logger,
            patch.object(
                AutomationEventService,
                '_send_to_automation_service',
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            service = AutomationEventService(mock_token_manager)
            await service.forward_event(
                provider=ProviderType.GITHUB,
                payload=github_org_payload,
                installation_id=99999,
            )

            mock_send.assert_not_called()
            mock_logger.warning.assert_called()
            assert 'not claimed' in str(mock_logger.warning.call_args)

    @pytest.mark.asyncio
    async def test_forward_bitbucket_dc_project_event_success(
        self, mock_token_manager, bitbucket_dc_pr_payload, mock_org_git_claim
    ):
        """
        GIVEN: A Bitbucket DC event from a claimed project
        WHEN: forward_event is called
        THEN: The project key is used as the git org for routing
        """
        from server.services.automation_event_service import AutomationEventService

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with (
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=mock_org_git_claim.org_id,
            ) as mock_resolver,
            patch(REDIS_PATCH, return_value=mock_redis),
            patch.object(
                AutomationEventService,
                '_send_to_automation_service',
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            service = AutomationEventService(mock_token_manager)
            await service.forward_event(
                provider=ProviderType.BITBUCKET_DATA_CENTER,
                payload=bitbucket_dc_pr_payload,
                installation_id='PROJ/myrepo',
            )

            mock_resolver.assert_awaited_once_with(
                provider='bitbucket_data_center',
                full_repo_name='proj/',
            )
            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args[0][0] == ProviderType.BITBUCKET_DATA_CENTER
            assert call_args[0][1] == mock_org_git_claim.org_id

            payload = call_args[0][2]
            assert payload['organization']['git_org'] == 'PROJ'
            assert payload['payload'] == bitbucket_dc_pr_payload

    @pytest.mark.asyncio
    async def test_forward_event_passes_the_delivery_id_through(
        self, mock_token_manager, bitbucket_dc_pr_payload, mock_org_git_claim
    ):
        """
        GIVEN: A Bitbucket DC event with its X-Request-Id
        WHEN: forward_event is called with that delivery id
        THEN: The send carries it, so the automation service can dedupe
        """
        from server.services.automation_event_service import AutomationEventService

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=str(mock_org_git_claim.org_id))

        with (
            patch(REDIS_PATCH, return_value=mock_redis),
            patch.object(
                AutomationEventService,
                '_send_to_automation_service',
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            service = AutomationEventService(mock_token_manager)
            await service.forward_event(
                provider=ProviderType.BITBUCKET_DATA_CENTER,
                payload=bitbucket_dc_pr_payload,
                installation_id='PROJ/myrepo',
                delivery_id='@1AB2CD3x4EF5GH6x7IJ8KL9x0',
            )

            mock_send.assert_awaited_once()
            assert mock_send.call_args.kwargs['delivery_id'] == (
                '@1AB2CD3x4EF5GH6x7IJ8KL9x0'
            )

    @pytest.mark.asyncio
    async def test_forward_azure_devops_event_success(
        self, mock_token_manager, azure_devops_pr_payload, mock_org_git_claim
    ):
        """
        GIVEN: An Azure DevOps event from a claimed organization
        WHEN: forward_event is called
        THEN: It is sent under the organization name without asking which
              event types the automation service wants
        """
        from server.services.automation_event_service import AutomationEventService

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with (
            patch.object(
                AutomationEventService,
                '_get_requested_event_metadata',
                new_callable=AsyncMock,
            ) as mock_requested_types,
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=mock_org_git_claim.org_id,
            ) as mock_resolver,
            patch(REDIS_PATCH, return_value=mock_redis),
            patch.object(
                AutomationEventService,
                '_send_to_automation_service',
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            service = AutomationEventService(mock_token_manager)
            await service.forward_event(
                provider=ProviderType.AZURE_DEVOPS,
                payload=azure_devops_pr_payload,
                installation_id='sub-1',
                event_type='git.pullrequest.created',
            )

            mock_requested_types.assert_not_awaited()
            mock_resolver.assert_awaited_once_with(
                provider='azure_devops',
                full_repo_name='fabrikam/',
            )
            mock_send.assert_called_once_with(
                ProviderType.AZURE_DEVOPS,
                mock_org_git_claim.org_id,
                {
                    'organization': {
                        'git_org': 'Fabrikam',
                        'openhands_org_id': str(mock_org_git_claim.org_id),
                    },
                    'payload': azure_devops_pr_payload,
                },
                # Azure DevOps hands over no delivery id, so none is forwarded.
                delivery_id=None,
            )


class TestExtractOwnerInfo:
    """Tests for _extract_owner_info method."""

    def test_extract_github_owner_info(self, mock_token_manager, github_org_payload):
        """
        GIVEN: A GitHub webhook payload
        WHEN: _extract_owner_info is called
        THEN: GitHub owner info is correctly extracted
        """
        service = create_service(mock_token_manager)
        git_org, owner_type, owner_id = service._extract_owner_info(
            ProviderType.GITHUB, github_org_payload
        )

        assert git_org == 'test-org'
        assert owner_type == 'Organization'
        assert owner_id == 789

    def test_extract_github_user_owner_info(
        self, mock_token_manager, github_user_payload
    ):
        """
        GIVEN: A GitHub webhook payload from a personal repo
        WHEN: _extract_owner_info is called
        THEN: User owner info is correctly extracted
        """
        service = create_service(mock_token_manager)
        git_org, owner_type, owner_id = service._extract_owner_info(
            ProviderType.GITHUB, github_user_payload
        )

        assert git_org == 'testuser'
        assert owner_type == 'User'
        assert owner_id == 12345

    def test_extract_bitbucket_dc_pr_owner_info(
        self, mock_token_manager, bitbucket_dc_pr_payload
    ):
        """
        GIVEN: A Bitbucket DC PR payload
        WHEN: _extract_owner_info is called
        THEN: The target repository project key is used as the org
        """
        service = create_service(mock_token_manager)
        git_org, owner_type, owner_id = service._extract_owner_info(
            ProviderType.BITBUCKET_DATA_CENTER, bitbucket_dc_pr_payload
        )

        assert git_org == 'PROJ'
        assert owner_type == 'Project'
        assert owner_id is None

    def test_extract_bitbucket_dc_repo_owner_info(
        self, mock_token_manager, bitbucket_dc_repo_payload
    ):
        """
        GIVEN: A Bitbucket DC repository payload
        WHEN: _extract_owner_info is called
        THEN: The repository project key is used as the org
        """
        service = create_service(mock_token_manager)
        git_org, owner_type, owner_id = service._extract_owner_info(
            ProviderType.BITBUCKET_DATA_CENTER, bitbucket_dc_repo_payload
        )

        assert git_org == 'PROJ'
        assert owner_type == 'Project'
        assert owner_id is None

    def test_extract_azure_devops_owner_info(
        self, mock_token_manager, azure_devops_pr_payload
    ):
        """
        GIVEN: An Azure DevOps Service Hook payload
        WHEN: _extract_owner_info is called
        THEN: The Azure DevOps organization is used as the org
        """
        service = create_service(mock_token_manager)
        git_org, owner_type, owner_id = service._extract_owner_info(
            ProviderType.AZURE_DEVOPS, azure_devops_pr_payload
        )

        assert git_org == 'Fabrikam'
        assert owner_type == 'Organization'
        assert owner_id is None

    def test_extract_azure_devops_owner_info_from_visualstudio_url(
        self, mock_token_manager
    ):
        """
        GIVEN: An Azure DevOps payload from an organization on visualstudio.com
        WHEN: _extract_owner_info is called
        THEN: The organization comes from the host name
        """
        service = create_service(mock_token_manager)
        git_org, _, _ = service._extract_owner_info(
            ProviderType.AZURE_DEVOPS,
            {
                'eventType': 'workitem.commented',
                'resourceContainers': {
                    'collection': {'baseUrl': 'https://fabrikam.visualstudio.com/'}
                },
            },
        )

        assert git_org == 'fabrikam'

    def test_extract_azure_devops_owner_info_without_organization(
        self, mock_token_manager
    ):
        """
        GIVEN: An Azure DevOps payload with no organization URL
        WHEN: _extract_owner_info is called
        THEN: No org is returned, so the event is skipped
        """
        service = create_service(mock_token_manager)
        git_org, _, _ = service._extract_owner_info(
            ProviderType.AZURE_DEVOPS, {'eventType': 'git.pullrequest.created'}
        )

        assert git_org is None


class TestBuildEventPayload:
    """Tests for _build_event_payload method."""

    def test_build_minimal_payload(self, mock_token_manager):
        """
        GIVEN: Org context and payload
        WHEN: _build_event_payload is called
        THEN: Minimal payload with only org + payload is returned
        """
        from server.services.automation_event_service import OrgContext

        service = create_service(mock_token_manager)

        org_context = OrgContext(
            org_id=uuid.UUID('12345678-1234-5678-1234-567812345678'),
            git_org='test-org',
        )
        test_payload = {'action': 'opened', 'sender': {'login': 'user'}}

        result = service._build_event_payload(org_context, test_payload)

        assert result == {
            'organization': {
                'git_org': 'test-org',
                'openhands_org_id': '12345678-1234-5678-1234-567812345678',
            },
            'payload': test_payload,
        }
        # Verify NO access_control in payload
        assert 'access_control' not in result


class TestSendToAutomationService:
    """Tests for _send_to_automation_service method."""

    @pytest.mark.asyncio
    async def test_send_success(self, mock_token_manager):
        """
        GIVEN: AUTOMATION_SERVICE_URL is configured
        WHEN: _send_to_automation_service is called
        THEN: Request is sent with correct signature and provider in URL
        """

        org_id = uuid.UUID('12345678-1234-5678-1234-567812345678')
        payload = {'organization': {'git_org': 'test'}, 'payload': {}}

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={'matched': 2})

        mock_post_context = MagicMock()
        mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance = MagicMock()
        mock_session_instance.post = MagicMock(return_value=mock_post_context)

        mock_session_context = MagicMock()
        mock_session_context.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                'server.services.automation_event_service.AUTOMATION_SERVICE_URL',
                'https://automation.example.com',
            ),
            patch(
                'server.services.automation_event_service.aiohttp.ClientSession',
                return_value=mock_session_context,
            ),
        ):
            service = create_service(mock_token_manager)
            await service._send_to_automation_service(
                ProviderType.GITHUB, org_id, payload
            )

            # Verify the POST was called
            mock_session_instance.post.assert_called_once()
            # Verify URL includes provider
            call_args = mock_session_instance.post.call_args
            url = call_args[0][0]
            assert '/github' in url
            assert str(org_id) in url

    @pytest.mark.asyncio
    async def test_send_includes_provider_in_url(self, mock_token_manager):
        """
        GIVEN: GitHub provider
        WHEN: _send_to_automation_service is called
        THEN: The URL includes the provider name
        """
        org_id = uuid.UUID('12345678-1234-5678-1234-567812345678')
        payload = {}

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={'matched': 0})

        mock_post_context = MagicMock()
        mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance = MagicMock()
        mock_session_instance.post = MagicMock(return_value=mock_post_context)

        mock_session_context = MagicMock()
        mock_session_context.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                'server.services.automation_event_service.AUTOMATION_SERVICE_URL',
                'https://automation.example.com',
            ),
            patch(
                'server.services.automation_event_service.aiohttp.ClientSession',
                return_value=mock_session_context,
            ),
        ):
            service = create_service(mock_token_manager)

            # Test GitHub
            await service._send_to_automation_service(
                ProviderType.GITHUB, org_id, payload
            )
            github_url = mock_session_instance.post.call_args_list[0][0][0]
            assert github_url.endswith('/github')

    @staticmethod
    def _mock_post_session():
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={'matched': 0})
        mock_post_context = MagicMock()
        mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_context.__aexit__ = AsyncMock(return_value=None)
        mock_session_instance = MagicMock()
        mock_session_instance.post = MagicMock(return_value=mock_post_context)
        mock_session_context = MagicMock()
        mock_session_context.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_context.__aexit__ = AsyncMock(return_value=None)
        return mock_session_context, mock_session_instance

    async def _send_source(self, mock_token_manager, **kwargs) -> dict:
        """Send one source event and return the headers of the POST."""
        mock_session_context, mock_session_instance = self._mock_post_session()
        with (
            patch(
                'server.services.automation_event_service.AUTOMATION_SERVICE_URL',
                'https://automation.example.com',
            ),
            patch(
                'server.services.automation_event_service.aiohttp.ClientSession',
                return_value=mock_session_context,
            ),
        ):
            service = create_service(mock_token_manager)
            await service._send_source_to_automation_service(
                org_id=uuid.UUID('12345678-1234-5678-1234-567812345678'),
                payload={'organization': {}, 'payload': {}},
                **kwargs,
            )
        mock_session_instance.post.assert_called_once()
        return mock_session_instance.post.call_args.kwargs['headers']

    @pytest.mark.asyncio
    async def test_send_names_the_delivery_in_the_shared_header(
        self, mock_token_manager
    ):
        """
        GIVEN: A forwarded event with the provider's delivery id
        WHEN: _send_source_to_automation_service is called
        THEN: The automation service receives it in the header it dedupes on,
              the same one for every built-in source
        """
        from server.services.automation_event_service import (
            AUTOMATION_DELIVERY_ID_HEADER,
        )

        assert AUTOMATION_DELIVERY_ID_HEADER == 'X-GitHub-Delivery'
        for source, delivery_id in (
            ('bitbucket_data_center', '@1AB2CD3x4EF5GH6x7IJ8KL9x0'),
            ('jira_dc', 'a1b2c3d4e5f6'),
            ('github', '72d3162e-cc78-11e3-81ab-4c9367dc0958'),
        ):
            headers = await self._send_source(
                mock_token_manager, source=source, delivery_id=delivery_id
            )
            assert headers['X-GitHub-Delivery'] == delivery_id
            assert headers['X-Hub-Signature-256'].startswith('sha256=')

    @pytest.mark.asyncio
    async def test_send_without_a_delivery_id_omits_the_header(
        self, mock_token_manager
    ):
        """An event the provider did not identify is forwarded as before."""
        headers = await self._send_source(mock_token_manager, source='azure_devops')
        assert 'X-GitHub-Delivery' not in headers

        headers = await self._send_source(
            mock_token_manager, source='bitbucket_data_center', delivery_id=None
        )
        assert 'X-GitHub-Delivery' not in headers

    @staticmethod
    def _jira_dc_metadata(org_ids: list[uuid.UUID]):
        from server.services.automation_event_service import (
            EventDetectionRule,
            RequestedEventTypesCacheEntry,
        )

        return RequestedEventTypesCacheEntry(
            event_types={'comment_created', 'jira:issue_created'},
            event_detection_rules=[
                EventDetectionRule(
                    event_type=event_type,
                    jmespath=f"webhookEvent == '{event_type}'",
                )
                for event_type in ('comment_created', 'jira:issue_created')
            ],
            loaded_at=0,
            org_ids=org_ids,
        )

    async def _forward_jira_dc(
        self, mock_token_manager, org_id, payload, metadata
    ) -> AsyncMock:
        from server.services.automation_event_service import AutomationEventService

        with (
            patch.object(
                AutomationEventService,
                '_get_requested_event_metadata',
                new_callable=AsyncMock,
                return_value=metadata,
            ),
            patch.object(
                AutomationEventService,
                '_send_source_to_automation_service',
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            service = create_service(mock_token_manager)
            await service.forward_jira_dc_event(
                org_id=org_id,
                payload=payload,
                workspace_name='jira.company.com',
                connection_id=7,
                delivery_id='sig123',
            )
        return mock_send

    @pytest.mark.asyncio
    async def test_forward_jira_dc_event_uses_jira_dc_source(self, mock_token_manager):
        """
        GIVEN: A Jira DC webhook and resolved OpenHands org
        WHEN: forward_jira_dc_event is called
        THEN: The event is forwarded to the jira_dc automation source
        """
        org_id = uuid.UUID('12345678-1234-5678-1234-567812345678')
        payload = {'webhookEvent': 'comment_created'}

        mock_send = await self._forward_jira_dc(
            mock_token_manager, org_id, payload, self._jira_dc_metadata([])
        )

        mock_send.assert_awaited_once()
        assert mock_send.call_args.kwargs['source'] == 'jira_dc'
        assert mock_send.call_args.kwargs['org_id'] == org_id
        assert mock_send.call_args.kwargs['payload'] == {
            'organization': {
                'jira_dc_workspace': 'jira.company.com',
                'jira_dc_connection_id': 7,
                'openhands_org_id': str(org_id),
            },
            'payload': payload,
        }

    @pytest.mark.asyncio
    async def test_forward_jira_dc_event_reaches_every_org_with_an_automation(
        self, mock_token_manager
    ):
        """
        GIVEN: Two orgs besides the workspace's have Jira DC automations
        WHEN: forward_jira_dc_event is called
        THEN: Each org receives the event once, with its own org id
        """
        workspace_org = uuid.UUID('12345678-1234-5678-1234-567812345678')
        other_org = uuid.UUID('87654321-4321-8765-4321-876543218765')
        third_org = uuid.UUID('11111111-2222-3333-4444-555555555555')

        mock_send = await self._forward_jira_dc(
            mock_token_manager,
            workspace_org,
            {'webhookEvent': 'comment_created'},
            self._jira_dc_metadata([other_org, workspace_org, third_org]),
        )

        sent = [call.kwargs for call in mock_send.call_args_list]
        assert [kwargs['org_id'] for kwargs in sent] == [
            workspace_org,
            other_org,
            third_org,
        ]
        assert [
            kwargs['payload']['organization']['openhands_org_id'] for kwargs in sent
        ] == [str(workspace_org), str(other_org), str(third_org)]

    @pytest.mark.asyncio
    async def test_forward_jira_dc_event_without_workspace_org(
        self, mock_token_manager
    ):
        """
        GIVEN: An install-wide workspace (no org) and an org with an automation
        WHEN: forward_jira_dc_event is called
        THEN: That org still receives the event
        """
        other_org = uuid.UUID('87654321-4321-8765-4321-876543218765')

        mock_send = await self._forward_jira_dc(
            mock_token_manager,
            None,
            {'webhookEvent': 'jira:issue_created'},
            self._jira_dc_metadata([other_org]),
        )

        mock_send.assert_awaited_once()
        assert mock_send.call_args.kwargs['org_id'] == other_org

    @pytest.mark.asyncio
    async def test_forward_jira_dc_event_skips_events_no_automation_can_match(
        self, mock_token_manager
    ):
        """An event type outside the automation service's catalog is not sent."""
        mock_send = await self._forward_jira_dc(
            mock_token_manager,
            uuid.UUID('12345678-1234-5678-1234-567812345678'),
            {'webhookEvent': 'jira:worklog_updated'},
            self._jira_dc_metadata([uuid.UUID('87654321-4321-8765-4321-876543218765')]),
        )

        mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_forward_jira_dc_event_falls_back_to_workspace_org(
        self, mock_token_manager
    ):
        """Without metadata (an older automation service) only the workspace's
        org receives the event, as before."""
        org_id = uuid.UUID('12345678-1234-5678-1234-567812345678')

        mock_send = await self._forward_jira_dc(
            mock_token_manager, org_id, {'webhookEvent': 'comment_created'}, None
        )

        mock_send.assert_awaited_once()
        assert mock_send.call_args.kwargs['org_id'] == org_id

    @pytest.mark.asyncio
    async def test_forward_jira_dc_event_names_the_delivery_for_every_org(
        self, mock_token_manager
    ):
        """Each org receives the same delivery id, so a Jira DC redelivery is
        dropped per org while the fan-out itself still reaches every org."""
        workspace_org = uuid.UUID('12345678-1234-5678-1234-567812345678')
        other_org = uuid.UUID('87654321-4321-8765-4321-876543218765')

        mock_send = await self._forward_jira_dc(
            mock_token_manager,
            workspace_org,
            {'webhookEvent': 'comment_created'},
            self._jira_dc_metadata([other_org]),
        )

        sent = [call.kwargs for call in mock_send.call_args_list]
        assert [kwargs['org_id'] for kwargs in sent] == [workspace_org, other_org]
        assert [kwargs['delivery_id'] for kwargs in sent] == ['sig123', 'sig123']

    @pytest.mark.asyncio
    async def test_fetch_requested_event_metadata_reads_org_ids(
        self, mock_token_manager
    ):
        other_org = uuid.UUID('87654321-4321-8765-4321-876543218765')
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                'source': 'jira_dc',
                'event_types': ['comment_created'],
                'event_detection_rules': [
                    {
                        'event_type': 'comment_created',
                        'jmespath': "webhookEvent == 'comment_created'",
                    }
                ],
                'org_ids': [str(other_org), 'not-a-uuid'],
            }
        )
        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_context.__aexit__ = AsyncMock(return_value=None)
        mock_session_instance = MagicMock()
        mock_session_instance.get = MagicMock(return_value=mock_get_context)
        mock_session_context = MagicMock()
        mock_session_context.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                'server.services.automation_event_service.AUTOMATION_SERVICE_URL',
                'https://automation.example.com',
            ),
            patch(
                'server.services.automation_event_service.aiohttp.ClientSession',
                return_value=mock_session_context,
            ),
        ):
            service = create_service(mock_token_manager)
            metadata = await service._fetch_requested_event_metadata('jira_dc')

        assert metadata is not None
        assert metadata.org_ids == [other_org]
        assert metadata.event_types == {'comment_created'}

    @pytest.mark.asyncio
    async def test_send_no_url_configured(self, mock_token_manager):
        """
        GIVEN: AUTOMATION_SERVICE_URL is not configured
        WHEN: _send_to_automation_service is called
        THEN: Warning is logged and nothing is sent
        """
        org_id = uuid.UUID('12345678-1234-5678-1234-567812345678')
        payload = {}

        with (
            patch(
                'server.services.automation_event_service.AUTOMATION_SERVICE_URL', None
            ),
            patch('server.services.automation_event_service.logger') as mock_logger,
        ):
            service = create_service(mock_token_manager)
            await service._send_to_automation_service(
                ProviderType.GITHUB, org_id, payload
            )

            mock_logger.warning.assert_called()
            assert 'not configured' in str(mock_logger.warning.call_args)

    @pytest.mark.asyncio
    async def test_send_timeout_logs_error(self, mock_token_manager):
        """
        GIVEN: AUTOMATION_SERVICE_URL is configured
        WHEN: _send_to_automation_service times out
        THEN: Error is logged (not warning) since the event was never delivered
        """
        import asyncio

        org_id = uuid.UUID('12345678-1234-5678-1234-567812345678')
        payload = {'test': 'data'}

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={'matched': 2})

        # Create a mock post that raises TimeoutError within the async context
        async def raise_timeout(*args, **kwargs):
            raise asyncio.TimeoutError('Connection timed out')

        mock_post_context = MagicMock()
        mock_post_context.__aenter__ = AsyncMock(
            side_effect=asyncio.TimeoutError('Connection timed out')
        )
        mock_post_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance = MagicMock()
        mock_session_instance.post = MagicMock(return_value=mock_post_context)

        mock_session_context = MagicMock()
        mock_session_context.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                'server.services.automation_event_service.AUTOMATION_SERVICE_URL',
                'https://automation.example.com',
            ),
            patch(
                'server.services.automation_event_service.aiohttp.ClientSession',
                return_value=mock_session_context,
            ),
            patch('server.services.automation_event_service.logger') as mock_logger,
        ):
            service = create_service(mock_token_manager)
            await service._send_to_automation_service(
                ProviderType.GITHUB, org_id, payload
            )

            mock_logger.exception.assert_called()
            assert 'Timeout' in str(mock_logger.exception.call_args)
            assert (
                'never delivered' in str(mock_logger.exception.call_args).lower()
                or 'timeout' in str(mock_logger.exception.call_args).lower()
            )

    @pytest.mark.asyncio
    async def test_send_5xx_logs_error(self, mock_token_manager):
        """
        GIVEN: Automation service returns 5xx (server error)
        WHEN: _send_to_automation_service is called
        THEN: Error is logged (event was dropped, user impact)
        """
        org_id = uuid.UUID('12345678-1234-5678-1234-567812345678')
        payload = {'organization': {'git_org': 'test'}, 'payload': {}}

        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.json = AsyncMock(return_value={'error': 'internal error'})

        mock_post_context = MagicMock()
        mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance = MagicMock()
        mock_session_instance.post = MagicMock(return_value=mock_post_context)

        mock_session_context = MagicMock()
        mock_session_context.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                'server.services.automation_event_service.AUTOMATION_SERVICE_URL',
                'https://automation.example.com',
            ),
            patch(
                'server.services.automation_event_service.aiohttp.ClientSession',
                return_value=mock_session_context,
            ),
            patch('server.services.automation_event_service.logger') as mock_logger,
        ):
            service = create_service(mock_token_manager)
            await service._send_to_automation_service(
                ProviderType.GITHUB, org_id, payload
            )

            mock_logger.error.assert_called()
            assert '500' in str(mock_logger.error.call_args)
            mock_logger.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_client_error_logs_error(self, mock_token_manager):
        """
        GIVEN: AUTOMATION_SERVICE_URL is configured
        WHEN: _send_to_automation_service fails with HTTP error
        THEN: Error is logged (not warning) since the event was not delivered
        """
        import aiohttp

        org_id = uuid.UUID('12345678-1234-5678-1234-567812345678')
        payload = {'test': 'data'}

        mock_session_instance = MagicMock()
        mock_session_instance.post = MagicMock()

        # Create a mock that raises ClientError when used as async context manager
        mock_post_context = MagicMock()
        mock_post_context.__aenter__ = AsyncMock(
            side_effect=aiohttp.ClientError('Connection refused')
        )
        mock_post_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.post = MagicMock(return_value=mock_post_context)

        mock_session_context = MagicMock()
        mock_session_context.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                'server.services.automation_event_service.AUTOMATION_SERVICE_URL',
                'https://automation.example.com',
            ),
            patch(
                'server.services.automation_event_service.aiohttp.ClientSession',
                return_value=mock_session_context,
            ),
            patch('server.services.automation_event_service.logger') as mock_logger,
        ):
            service = create_service(mock_token_manager)
            await service._send_to_automation_service(
                ProviderType.GITHUB, org_id, payload
            )

            mock_logger.exception.assert_called()
            assert 'HTTP error' in str(mock_logger.exception.call_args)

    @pytest.mark.asyncio
    async def test_send_503_logs_error(self, mock_token_manager):
        """
        GIVEN: Automation service returns 503 (service unavailable)
        WHEN: _send_to_automation_service is called
        THEN: Error is logged (event was dropped, user impact)
        """
        import aiohttp

        org_id = uuid.UUID('12345678-1234-5678-1234-567812345678')
        payload = {'organization': {'git_org': 'test'}, 'payload': {}}

        mock_response = MagicMock()
        mock_response.status = 503
        # Simulate JSON parse failure (e.g., load balancer 503 returns text)
        mock_response.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(
                request_info=MagicMock(),
                history=(),
            )
        )
        mock_response.text = AsyncMock(return_value='Service Temporarily Unavailable')

        mock_post_context = MagicMock()
        mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance = MagicMock()
        mock_session_instance.post = MagicMock(return_value=mock_post_context)

        mock_session_context = MagicMock()
        mock_session_context.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                'server.services.automation_event_service.AUTOMATION_SERVICE_URL',
                'https://automation.example.com',
            ),
            patch(
                'server.services.automation_event_service.aiohttp.ClientSession',
                return_value=mock_session_context,
            ),
            patch('server.services.automation_event_service.logger') as mock_logger,
        ):
            service = create_service(mock_token_manager)
            await service._send_to_automation_service(
                ProviderType.GITHUB, org_id, payload
            )

            mock_logger.error.assert_called()
            assert '503' in str(mock_logger.error.call_args)
            mock_logger.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_4xx_logs_error(self, mock_token_manager):
        """
        GIVEN: Automation service returns 4xx (client error)
        WHEN: _send_to_automation_service is called
        THEN: Error is logged because the event was not delivered
        """
        org_id = uuid.UUID('12345678-1234-5678-1234-567812345678')
        payload = {'organization': {'git_org': 'test'}, 'payload': {}}

        mock_response = MagicMock()
        mock_response.status = 400
        mock_response.json = AsyncMock(return_value={'error': 'bad request'})

        mock_post_context = MagicMock()
        mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance = MagicMock()
        mock_session_instance.post = MagicMock(return_value=mock_post_context)

        mock_session_context = MagicMock()
        mock_session_context.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                'server.services.automation_event_service.AUTOMATION_SERVICE_URL',
                'https://automation.example.com',
            ),
            patch(
                'server.services.automation_event_service.aiohttp.ClientSession',
                return_value=mock_session_context,
            ),
            patch('server.services.automation_event_service.logger') as mock_logger,
        ):
            service = create_service(mock_token_manager)
            await service._send_to_automation_service(
                ProviderType.GITHUB, org_id, payload
            )

            mock_logger.error.assert_called()
            assert '400' in str(mock_logger.error.call_args)
            mock_logger.warning.assert_not_called()


class TestSignPayload:
    """Tests for _sign_payload method."""

    def test_sign_payload(self, mock_token_manager):
        """
        GIVEN: A payload bytes
        WHEN: _sign_payload is called
        THEN: HMAC-SHA256 signature is returned in correct format
        """
        with patch(
            'server.services.automation_event_service.AUTOMATION_WEBHOOK_SECRET',
            'test-shared-secret',
        ):
            service = create_service(mock_token_manager)
            payload_bytes = b'{"test": "data"}'

            signature = service._sign_payload(payload_bytes)

            assert signature.startswith('sha256=')
            assert len(signature) == 71  # 'sha256=' + 64 hex chars

    def test_sign_payload_uses_dedicated_secret(self, mock_token_manager):
        """
        GIVEN: AUTOMATION_WEBHOOK_SECRET is configured
        WHEN: _sign_payload is called
        THEN: The dedicated secret is used (not GitHub webhook secret)
        """
        import hashlib
        import hmac

        # Use the default test secret from CONSTANT_PATCHES
        shared_secret = 'test-shared-secret'
        payload_bytes = b'{"test": "data"}'

        # Calculate expected signature with the shared secret
        expected_sig = hmac.new(
            shared_secret.encode('utf-8'),
            msg=payload_bytes,
            digestmod=hashlib.sha256,
        ).hexdigest()

        with patch(
            'server.services.automation_event_service.AUTOMATION_WEBHOOK_SECRET',
            shared_secret,
        ):
            service = create_service(mock_token_manager)
            signature = service._sign_payload(payload_bytes)

            assert signature == f'sha256={expected_sig}'


class TestCacheHelpers:
    """Tests for generic cache helper methods."""

    @pytest.mark.asyncio
    async def test_get_cached_value_hit(self, mock_token_manager):
        """
        GIVEN: Value exists in Redis cache
        WHEN: _get_cached_value is called
        THEN: Decoded string value is returned
        """
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=b'cached-value')

        with patch(REDIS_PATCH, return_value=mock_redis):
            service = create_service(mock_token_manager)
            result = await service._get_cached_value('test-key')

            assert result == 'cached-value'

    @pytest.mark.asyncio
    async def test_get_cached_value_miss(self, mock_token_manager):
        """
        GIVEN: Value does not exist in Redis cache
        WHEN: _get_cached_value is called
        THEN: None is returned
        """
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch(REDIS_PATCH, return_value=mock_redis):
            service = create_service(mock_token_manager)
            result = await service._get_cached_value('test-key')

            assert result is None

    @pytest.mark.asyncio
    async def test_get_cached_value_redis_unavailable(self, mock_token_manager):
        """
        GIVEN: Redis is unavailable
        WHEN: _get_cached_value is called
        THEN: None is returned (graceful degradation)
        """
        with patch(REDIS_PATCH, return_value=None):
            service = create_service(mock_token_manager)
            result = await service._get_cached_value('test-key')

            assert result is None

    @pytest.mark.asyncio
    async def test_set_cached_value_success(self, mock_token_manager):
        """
        GIVEN: Redis is available
        WHEN: _set_cached_value is called
        THEN: Value is stored with TTL
        """
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()

        with patch(REDIS_PATCH, return_value=mock_redis):
            service = create_service(mock_token_manager)
            await service._set_cached_value('test-key', 'test-value', 3600)

            mock_redis.setex.assert_called_once_with('test-key', 3600, 'test-value')

    @pytest.mark.asyncio
    async def test_set_cached_value_redis_unavailable(self, mock_token_manager):
        """
        GIVEN: Redis is unavailable
        WHEN: _set_cached_value is called
        THEN: No error is raised (silent failure)
        """
        with patch(REDIS_PATCH, return_value=None):
            service = create_service(mock_token_manager)
            # Should not raise
            await service._set_cached_value('test-key', 'test-value', 3600)


class TestResolveDefaultOrgFallback:
    """Tests for the single-org default-org fallback in org resolution."""

    @staticmethod
    def _team_org(name='AcmeOrg'):
        org = MagicMock()
        org.id = uuid.UUID('87654321-4321-8765-4321-876543218765')
        org.name = name
        return org

    @pytest.mark.asyncio
    async def test_fallback_resolves_single_team_org(
        self, mock_token_manager, monkeypatch
    ):
        """
        GIVEN: A configured default org that is the only team org, no claim
        WHEN: _resolve_default_org_fallback is called
        THEN: The default org id is returned and cached
        """
        monkeypatch.setenv('OPENHANDS_DEFAULT_ORG_ENABLED', 'true')
        org = self._team_org()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with (
            patch(
                'server.services.automation_event_service.OrgStore'
            ) as mock_org_store,
            patch(REDIS_PATCH, return_value=mock_redis),
        ):
            mock_org_store.get_default_org = AsyncMock(return_value=org)
            mock_org_store.count_team_orgs = AsyncMock(return_value=1)
            service = create_service(mock_token_manager)
            result = await service._resolve_default_org_fallback(
                ProviderType.BITBUCKET_DATA_CENTER, 'proj'
            )

            assert result == org.id
            mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_disabled_without_default_org_config(
        self, mock_token_manager, monkeypatch
    ):
        """
        GIVEN: No default org configured
        WHEN: _resolve_default_org_fallback is called
        THEN: None is returned without touching the DB
        """
        monkeypatch.delenv('OPENHANDS_DEFAULT_ORG_ENABLED', raising=False)
        mock_redis = AsyncMock()

        with (
            patch(
                'server.services.automation_event_service.OrgStore'
            ) as mock_org_store,
            patch(REDIS_PATCH, return_value=mock_redis),
        ):
            mock_org_store.get_default_org = AsyncMock()
            service = create_service(mock_token_manager)
            result = await service._resolve_default_org_fallback(
                ProviderType.BITBUCKET_DATA_CENTER, 'proj'
            )

            assert result is None
            mock_org_store.get_default_org.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_off_with_multiple_team_orgs(
        self, mock_token_manager, monkeypatch
    ):
        """
        GIVEN: A default org configured but a second team org exists
        WHEN: _resolve_default_org_fallback is called
        THEN: None is returned (multi-org installs require explicit claims)
        """
        monkeypatch.setenv('OPENHANDS_DEFAULT_ORG_ENABLED', 'true')
        org = self._team_org()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with (
            patch(
                'server.services.automation_event_service.OrgStore'
            ) as mock_org_store,
            patch(REDIS_PATCH, return_value=mock_redis),
        ):
            mock_org_store.get_default_org = AsyncMock(return_value=org)
            mock_org_store.count_team_orgs = AsyncMock(return_value=2)
            service = create_service(mock_token_manager)
            result = await service._resolve_default_org_fallback(
                ProviderType.BITBUCKET_DATA_CENTER, 'proj'
            )

            assert result is None

    @pytest.mark.asyncio
    async def test_fallback_cache_hit_skips_db(self, mock_token_manager, monkeypatch):
        """
        GIVEN: A cached fallback result
        WHEN: _resolve_default_org_fallback is called
        THEN: The cached org id is returned without DB lookups
        """
        monkeypatch.setenv('OPENHANDS_DEFAULT_ORG_ENABLED', 'true')
        cached_id = '87654321-4321-8765-4321-876543218765'
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=cached_id.encode())

        with (
            patch(
                'server.services.automation_event_service.OrgStore'
            ) as mock_org_store,
            patch(REDIS_PATCH, return_value=mock_redis),
        ):
            mock_org_store.get_default_org = AsyncMock()
            service = create_service(mock_token_manager)
            result = await service._resolve_default_org_fallback(
                ProviderType.BITBUCKET_DATA_CENTER, 'proj'
            )

            assert result == uuid.UUID(cached_id)
            mock_org_store.get_default_org.assert_not_called()

    @pytest.mark.asyncio
    async def test_unclaimed_bitbucket_dc_event_routes_to_default_org(
        self, mock_token_manager, bitbucket_dc_pr_payload, monkeypatch
    ):
        """
        GIVEN: An unclaimed Bitbucket DC project on a single-org install
        WHEN: _resolve_org_context is called
        THEN: The event resolves to the default org instead of being dropped
        """
        monkeypatch.setenv('OPENHANDS_DEFAULT_ORG_ENABLED', 'true')
        org = self._team_org()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with (
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=None,  # No claim
            ),
            patch(
                'server.services.automation_event_service.OrgStore'
            ) as mock_org_store,
            patch(REDIS_PATCH, return_value=mock_redis),
        ):
            mock_org_store.get_default_org = AsyncMock(return_value=org)
            mock_org_store.count_team_orgs = AsyncMock(return_value=1)
            service = create_service(mock_token_manager)
            context = await service._resolve_org_context(
                ProviderType.BITBUCKET_DATA_CENTER, bitbucket_dc_pr_payload
            )

            assert context is not None
            assert context.org_id == org.id
            assert context.git_org == 'PROJ'

    @pytest.mark.asyncio
    async def test_unclaimed_azure_devops_event_routes_to_default_org(
        self, mock_token_manager, azure_devops_pr_payload, monkeypatch
    ):
        """
        GIVEN: An unclaimed Azure DevOps organization on a single-org install
        WHEN: _resolve_org_context is called
        THEN: The event resolves to the default org instead of being dropped
        """
        monkeypatch.setenv('OPENHANDS_DEFAULT_ORG_ENABLED', 'true')
        org = self._team_org()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        with (
            patch(
                'server.services.automation_event_service.resolve_org_for_repo',
                new_callable=AsyncMock,
                return_value=None,  # No claim
            ),
            patch(
                'server.services.automation_event_service.OrgStore'
            ) as mock_org_store,
            patch(REDIS_PATCH, return_value=mock_redis),
        ):
            mock_org_store.get_default_org = AsyncMock(return_value=org)
            mock_org_store.count_team_orgs = AsyncMock(return_value=1)
            service = create_service(mock_token_manager)
            context = await service._resolve_org_context(
                ProviderType.AZURE_DEVOPS, azure_devops_pr_payload
            )

            assert context is not None
            assert context.org_id == org.id
            assert context.git_org == 'Fabrikam'
