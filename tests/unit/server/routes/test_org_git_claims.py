"""Tests for Git organization claim API endpoints.

Tests the following endpoints:
- GET /api/organizations/{org_id}/git-claims (list claims)
- POST /api/organizations/{org_id}/git-claims (claim)
- DELETE /api/organizations/{org_id}/git-claims/{claim_id} (disconnect)
"""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.user_auth import get_user_id
from server.routes.org_models import VALID_GIT_PROVIDERS, GitOrgClaimRequest
from server.routes.orgs import (
    claim_git_organization,
    disconnect_git_organization,
    get_git_claims,
    org_router,
)
from server.services.automation_event_service import AutomationEventService
from storage.org_git_claim import OrgGitClaim

TEST_USER_ID = str(uuid.uuid4())
REDIS_PATCH = 'server.services.automation_event_service.get_redis_client_async'
RESOLVER_PATCH = 'server.services.automation_event_service.resolve_org_for_repo'


class _DictRedis:
    """In-memory stand-in for the async Redis client; unit tests have no Redis."""

    def __init__(self):
        self.data: dict[str, str] = {}
        self.deleted: list[str] = []

    async def get(self, key):
        return self.data.get(key)

    async def setex(self, key, ttl, value):
        self.data[key] = value

    async def delete(self, *keys):
        self.deleted.extend(keys)
        return sum(self.data.pop(key, None) is not None for key in keys)


@pytest.fixture(autouse=True)
def claim_cache():
    """The Redis cache of claim lookups that automation event routing reads."""
    redis = _DictRedis()
    with patch(REDIS_PATCH, return_value=redis):
        yield redis


@pytest.fixture
def org_id():
    return uuid.uuid4()


@pytest.fixture
def user_id():
    return str(uuid.uuid4())


@pytest.fixture
def make_claim():
    """Factory to create mock OrgGitClaim objects."""

    def _make(org_id, provider='github', git_organization='OpenHands', claimed_by=None):
        claim = MagicMock(spec=OrgGitClaim)
        claim.id = uuid.uuid4()
        claim.org_id = org_id
        claim.provider = provider
        claim.git_organization = git_organization
        claim.claimed_by = claimed_by or uuid.uuid4()
        claim.claimed_at = datetime(2026, 4, 1, 12, 0, 0)
        return claim

    return _make


# =============================================================================
# GET /api/organizations/{org_id}/git-claims
# =============================================================================


class TestGetGitClaims:
    """Tests for the get Git organization claims endpoint."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_claims(self, org_id, user_id):
        """
        GIVEN: An organization with no Git claims
        WHEN: GET /api/organizations/{org_id}/git-claims is called
        THEN: An empty list is returned
        """
        with patch(
            'server.routes.orgs.OrgGitClaimStore.get_claims_by_org_id',
            AsyncMock(return_value=[]),
        ) as mock_get:
            result = await get_git_claims(org_id=org_id, user_id=user_id)

        assert result == []
        mock_get.assert_called_once_with(org_id=org_id)

    @pytest.mark.asyncio
    async def test_returns_claims_for_organization(self, org_id, user_id, make_claim):
        """
        GIVEN: An organization with multiple Git claims
        WHEN: GET /api/organizations/{org_id}/git-claims is called
        THEN: All claims are returned with correct details
        """
        claim1 = make_claim(org_id, provider='github', git_organization='OpenHands')
        claim2 = make_claim(org_id, provider='gitlab', git_organization='AcmeCo')

        with patch(
            'server.routes.orgs.OrgGitClaimStore.get_claims_by_org_id',
            AsyncMock(return_value=[claim1, claim2]),
        ):
            result = await get_git_claims(org_id=org_id, user_id=user_id)

        assert len(result) == 2
        assert result[0].id == str(claim1.id)
        assert result[0].org_id == str(org_id)
        assert result[0].provider == 'github'
        assert result[0].git_organization == 'OpenHands'
        assert result[0].claimed_by == str(claim1.claimed_by)
        assert result[0].claimed_at == '2026-04-01T12:00:00'
        assert result[1].id == str(claim2.id)
        assert result[1].provider == 'gitlab'
        assert result[1].git_organization == 'AcmeCo'

    @pytest.mark.asyncio
    async def test_returns_500_on_unexpected_error(self, org_id, user_id):
        """
        GIVEN: An unexpected error occurs when fetching claims
        WHEN: GET /api/organizations/{org_id}/git-claims is called
        THEN: A 500 Internal Server Error is returned
        """
        with patch(
            'server.routes.orgs.OrgGitClaimStore.get_claims_by_org_id',
            AsyncMock(side_effect=RuntimeError('db connection failed')),
        ):
            with pytest.raises(Exception) as exc_info:
                await get_git_claims(org_id=org_id, user_id=user_id)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


# =============================================================================
# POST /api/organizations/{org_id}/git-claims
# =============================================================================


class TestClaimGitOrganization:
    """Tests for the claim Git organization endpoint."""

    @pytest.mark.asyncio
    async def test_claim_succeeds_for_unclaimed_org(self, org_id, user_id, make_claim):
        """
        GIVEN: A Git organization that has not been claimed
        WHEN: POST /api/organizations/{org_id}/git-claims is called
        THEN: The claim is created and returned with correct details
        """
        # Arrange
        mock_claim = make_claim(org_id, claimed_by=uuid.UUID(user_id))
        request = MagicMock()
        request.provider = 'github'
        request.git_organization = 'OpenHands'

        with (
            patch(
                'server.routes.orgs.OrgGitClaimStore.get_claim_by_provider_and_git_org',
                AsyncMock(return_value=None),
            ),
            patch(
                'server.routes.orgs.OrgGitClaimStore.create_claim',
                AsyncMock(return_value=mock_claim),
            ) as mock_create,
        ):
            # Act
            response = await claim_git_organization(
                org_id=org_id, request=request, user_id=user_id
            )

        # Assert
        assert response.id == str(mock_claim.id)
        assert response.org_id == str(org_id)
        assert response.provider == 'github'
        assert response.git_organization == 'OpenHands'
        assert response.claimed_by == user_id
        mock_create.assert_called_once_with(
            org_id=org_id,
            provider='github',
            git_organization='OpenHands',
            claimed_by=uuid.UUID(user_id),
        )

    @pytest.mark.asyncio
    async def test_claim_fails_when_already_claimed(self, org_id, user_id, make_claim):
        """
        GIVEN: A Git organization already claimed by another OpenHands org
        WHEN: POST /api/organizations/{org_id}/git-claims is called
        THEN: A 409 Conflict error is returned
        """
        # Arrange
        other_org_id = uuid.uuid4()
        existing_claim = make_claim(
            other_org_id, provider='github', git_organization='AlreadyClaimed'
        )
        request = MagicMock()
        request.provider = 'github'
        request.git_organization = 'AlreadyClaimed'

        with patch(
            'server.routes.orgs.OrgGitClaimStore.get_claim_by_provider_and_git_org',
            AsyncMock(return_value=existing_claim),
        ):
            # Act & Assert
            with pytest.raises(Exception) as exc_info:
                await claim_git_organization(
                    org_id=org_id, request=request, user_id=user_id
                )

            assert exc_info.value.status_code == status.HTTP_409_CONFLICT

    @pytest.mark.asyncio
    async def test_claim_returns_500_on_unexpected_error(self, org_id, user_id):
        """
        GIVEN: An unexpected error occurs during claim creation
        WHEN: POST /api/organizations/{org_id}/git-claims is called
        THEN: A 500 Internal Server Error is returned
        """
        # Arrange
        request = MagicMock()
        request.provider = 'github'
        request.git_organization = 'OpenHands'

        with patch(
            'server.routes.orgs.OrgGitClaimStore.get_claim_by_provider_and_git_org',
            AsyncMock(side_effect=RuntimeError('db connection failed')),
        ):
            # Act & Assert
            with pytest.raises(Exception) as exc_info:
                await claim_git_organization(
                    org_id=org_id, request=request, user_id=user_id
                )

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    @pytest.mark.asyncio
    async def test_claim_race_condition_returns_409(self, org_id, user_id):
        """
        GIVEN: Pre-check passes but a concurrent request claims the org first
        WHEN: create_claim raises IntegrityError (DB unique constraint)
        THEN: A 409 Conflict error is returned instead of 500
        """
        # Arrange
        request = MagicMock()
        request.provider = 'github'
        request.git_organization = 'RaceOrg'

        with (
            patch(
                'server.routes.orgs.OrgGitClaimStore.get_claim_by_provider_and_git_org',
                AsyncMock(return_value=None),
            ),
            patch(
                'server.routes.orgs.OrgGitClaimStore.create_claim',
                AsyncMock(
                    side_effect=IntegrityError(
                        'duplicate',
                        '',
                        Exception('uq_provider_git_org'),
                    )
                ),
            ),
        ):
            # Act & Assert
            with pytest.raises(Exception) as exc_info:
                await claim_git_organization(
                    org_id=org_id, request=request, user_id=user_id
                )

            assert exc_info.value.status_code == status.HTTP_409_CONFLICT


# =============================================================================
# DELETE /api/organizations/{org_id}/git-claims/{claim_id}
# =============================================================================


class TestDisconnectGitOrganization:
    """Tests for the disconnect Git organization endpoint."""

    @pytest.mark.asyncio
    async def test_disconnect_succeeds_for_existing_claim(
        self, org_id, user_id, make_claim
    ):
        """
        GIVEN: A valid claim belonging to the organization
        WHEN: DELETE /api/organizations/{org_id}/git-claims/{claim_id} is called
        THEN: The claim is deleted and a success message is returned
        """
        # Arrange
        claim = make_claim(org_id, git_organization='openhands')

        with patch(
            'server.routes.orgs.OrgGitClaimStore.delete_claim',
            AsyncMock(return_value=claim),
        ) as mock_delete:
            # Act
            result = await disconnect_git_organization(
                org_id=org_id, claim_id=claim.id, user_id=user_id
            )

        # Assert
        assert result == {'message': 'Git organization claim removed successfully'}
        mock_delete.assert_called_once_with(claim_id=claim.id, org_id=org_id)

    @pytest.mark.asyncio
    async def test_disconnect_fails_when_claim_not_found(self, org_id, user_id):
        """
        GIVEN: A claim_id that does not exist for this organization
        WHEN: DELETE /api/organizations/{org_id}/git-claims/{claim_id} is called
        THEN: A 404 Not Found error is returned
        """
        # Arrange
        claim_id = uuid.uuid4()

        with patch(
            'server.routes.orgs.OrgGitClaimStore.delete_claim',
            AsyncMock(return_value=None),
        ):
            # Act & Assert
            with pytest.raises(Exception) as exc_info:
                await disconnect_git_organization(
                    org_id=org_id, claim_id=claim_id, user_id=user_id
                )

            assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_disconnect_returns_500_on_unexpected_error(self, org_id, user_id):
        """
        GIVEN: An unexpected error occurs during claim deletion
        WHEN: DELETE /api/organizations/{org_id}/git-claims/{claim_id} is called
        THEN: A 500 Internal Server Error is returned
        """
        # Arrange
        claim_id = uuid.uuid4()

        with patch(
            'server.routes.orgs.OrgGitClaimStore.delete_claim',
            AsyncMock(side_effect=RuntimeError('db connection failed')),
        ):
            # Act & Assert
            with pytest.raises(Exception) as exc_info:
                await disconnect_git_organization(
                    org_id=org_id, claim_id=claim_id, user_id=user_id
                )

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


# =============================================================================
# Cached claim lookups used to route automation events
# =============================================================================


def _cache_key(provider: str, git_organization: str) -> str:
    return f'automation:org_claim:{provider}:{git_organization}'


class TestGitClaimChangesClearCachedLookup:
    """Webhook events cache claim lookups for an hour; claim changes clear them."""

    @staticmethod
    async def _route_event(provider: str, resolved_org_id):
        """Look the Git org up the way an incoming webhook event does."""
        service = AutomationEventService(MagicMock())
        with patch(RESOLVER_PATCH, AsyncMock(return_value=resolved_org_id)):
            return await service._resolve_git_org(ProviderType(provider), 'OpenHands')

    @staticmethod
    def _store_echo(make_claim):
        """create_claim stand-in returning a claim with the validated values."""

        async def create_claim(org_id, provider, git_organization, claimed_by):
            return make_claim(
                org_id,
                provider=provider,
                git_organization=git_organization,
                claimed_by=claimed_by,
            )

        return create_claim

    @pytest.mark.asyncio
    @pytest.mark.parametrize('provider', sorted(VALID_GIT_PROVIDERS))
    async def test_claim_clears_the_miss_cached_by_an_earlier_event(
        self, provider, org_id, user_id, make_claim, claim_cache
    ):
        """
        GIVEN: An event for the unclaimed Git org cached "not claimed"
        WHEN: An admin claims it, typed with other casing and spaces
        THEN: That lookup is cleared, other entries stay, and the next event
              routes to the org
        """
        assert await self._route_event(provider, None) is None
        assert claim_cache.data == {_cache_key(provider, 'openhands'): 'none'}
        other_provider = next(p for p in sorted(VALID_GIT_PROVIDERS) if p != provider)
        unrelated = {
            _cache_key(other_provider, 'openhands'): 'none',
            _cache_key(provider, 'other-org'): 'none',
        }
        claim_cache.data.update(unrelated)

        with (
            patch(
                'server.routes.orgs.OrgGitClaimStore.get_claim_by_provider_and_git_org',
                AsyncMock(return_value=None),
            ),
            patch(
                'server.routes.orgs.OrgGitClaimStore.create_claim',
                AsyncMock(side_effect=self._store_echo(make_claim)),
            ),
        ):
            response = await claim_git_organization(
                org_id=org_id,
                request=GitOrgClaimRequest(
                    provider=f' {provider.upper()} ', git_organization='  OpenHands '
                ),
                user_id=user_id,
            )

        assert response.provider == provider
        assert response.git_organization == 'openhands'
        assert claim_cache.data == unrelated
        assert await self._route_event(provider, org_id) == org_id

    @pytest.mark.asyncio
    async def test_claim_with_nothing_cached_routes_the_next_event(
        self, org_id, user_id, make_claim, claim_cache
    ):
        """
        GIVEN: No cached lookup for the Git org
        WHEN: An admin claims it
        THEN: The claim succeeds and the next event reads it from the database
        """
        with (
            patch(
                'server.routes.orgs.OrgGitClaimStore.get_claim_by_provider_and_git_org',
                AsyncMock(return_value=None),
            ),
            patch(
                'server.routes.orgs.OrgGitClaimStore.create_claim',
                AsyncMock(side_effect=self._store_echo(make_claim)),
            ),
        ):
            await claim_git_organization(
                org_id=org_id,
                request=GitOrgClaimRequest(
                    provider='github', git_organization='OpenHands'
                ),
                user_id=user_id,
            )

        assert claim_cache.data == {}
        assert await self._route_event('github', org_id) == org_id
        assert claim_cache.data == {_cache_key('github', 'openhands'): str(org_id)}

    @pytest.mark.asyncio
    async def test_disconnect_clears_the_org_cached_by_an_earlier_event(
        self, org_id, user_id, make_claim, claim_cache
    ):
        """
        GIVEN: Events for a claimed Bitbucket DC project cached its org
        WHEN: The claim is removed
        THEN: The cached org is cleared, so the next event no longer routes there
        """
        assert await self._route_event('bitbucket_data_center', org_id) == org_id
        claim = make_claim(
            org_id, provider='bitbucket_data_center', git_organization='openhands'
        )

        with patch(
            'server.routes.orgs.OrgGitClaimStore.delete_claim',
            AsyncMock(return_value=claim),
        ):
            await disconnect_git_organization(
                org_id=org_id, claim_id=claim.id, user_id=user_id
            )

        assert claim_cache.data == {}
        assert await self._route_event('bitbucket_data_center', None) is None

    @pytest.mark.asyncio
    async def test_disconnect_of_a_missing_claim_leaves_the_cache_alone(
        self, org_id, user_id, claim_cache
    ):
        """
        GIVEN: A claim id that does not exist for the organization
        WHEN: It is disconnected
        THEN: The route answers 404 without touching the cache
        """
        claim_cache.data[_cache_key('github', 'openhands')] = str(org_id)

        with patch(
            'server.routes.orgs.OrgGitClaimStore.delete_claim',
            AsyncMock(return_value=None),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await disconnect_git_organization(
                    org_id=org_id, claim_id=uuid.uuid4(), user_id=user_id
                )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert claim_cache.deleted == []
        assert claim_cache.data == {_cache_key('github', 'openhands'): str(org_id)}

    @pytest.mark.asyncio
    async def test_rejected_claim_keeps_the_cached_owner(
        self, org_id, user_id, make_claim, claim_cache
    ):
        """
        GIVEN: The Git org is already claimed and its org is cached
        WHEN: Another organization tries to claim it
        THEN: The 409 leaves the cached org in place
        """
        owner_org_id = uuid.uuid4()
        claim_cache.data[_cache_key('github', 'openhands')] = str(owner_org_id)

        with patch(
            'server.routes.orgs.OrgGitClaimStore.get_claim_by_provider_and_git_org',
            AsyncMock(return_value=make_claim(owner_org_id)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await claim_git_organization(
                    org_id=org_id,
                    request=GitOrgClaimRequest(
                        provider='github', git_organization='OpenHands'
                    ),
                    user_id=user_id,
                )

        assert exc_info.value.status_code == status.HTTP_409_CONFLICT
        assert claim_cache.deleted == []
        assert claim_cache.data == {
            _cache_key('github', 'openhands'): str(owner_org_id)
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize('failure', ['client', 'delete'])
    async def test_claim_succeeds_when_the_cache_cannot_be_cleared(
        self, failure, org_id, user_id, make_claim
    ):
        """
        GIVEN: Redis is unavailable or errors on delete
        WHEN: An admin claims a Git org
        THEN: The claim is still created and a warning is logged
        """
        redis = AsyncMock()
        redis.delete = AsyncMock(side_effect=ConnectionError('redis down'))
        if failure == 'client':
            redis_patch = patch(REDIS_PATCH, side_effect=ConnectionError('redis down'))
        else:
            redis_patch = patch(REDIS_PATCH, return_value=redis)

        with (
            redis_patch,
            patch('server.services.automation_event_service.logger') as logger,
            patch(
                'server.routes.orgs.OrgGitClaimStore.get_claim_by_provider_and_git_org',
                AsyncMock(return_value=None),
            ),
            patch(
                'server.routes.orgs.OrgGitClaimStore.create_claim',
                AsyncMock(side_effect=self._store_echo(make_claim)),
            ),
        ):
            response = await claim_git_organization(
                org_id=org_id,
                request=GitOrgClaimRequest(
                    provider='azure_devops', git_organization='Fabrikam'
                ),
                user_id=user_id,
            )

        assert response.git_organization == 'fabrikam'
        logger.warning.assert_called_once()
        assert 'azure_devops org fabrikam' in logger.warning.call_args.args[0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize('failure', ['client', 'delete'])
    async def test_disconnect_succeeds_when_the_cache_cannot_be_cleared(
        self, failure, org_id, user_id, make_claim
    ):
        """
        GIVEN: Redis is unavailable or errors on delete
        WHEN: An admin removes a claim
        THEN: The claim is still removed and a warning is logged
        """
        redis = AsyncMock()
        redis.delete = AsyncMock(side_effect=ConnectionError('redis down'))
        if failure == 'client':
            redis_patch = patch(REDIS_PATCH, side_effect=ConnectionError('redis down'))
        else:
            redis_patch = patch(REDIS_PATCH, return_value=redis)
        claim = make_claim(org_id, provider='gitlab', git_organization='acme')

        with (
            redis_patch,
            patch('server.services.automation_event_service.logger') as logger,
            patch(
                'server.routes.orgs.OrgGitClaimStore.delete_claim',
                AsyncMock(return_value=claim),
            ) as mock_delete,
        ):
            result = await disconnect_git_organization(
                org_id=org_id, claim_id=claim.id, user_id=user_id
            )

        assert result == {'message': 'Git organization claim removed successfully'}
        mock_delete.assert_awaited_once_with(claim_id=claim.id, org_id=org_id)
        logger.warning.assert_called_once()
        assert 'gitlab org acme' in logger.warning.call_args.args[0]


# =============================================================================
# Validation tests for GitOrgClaimRequest
# =============================================================================


class TestGitOrgClaimRequestValidation:
    """Tests for request model validation."""

    def test_valid_providers_are_accepted(self):
        """Each supported provider is accepted and normalized to lowercase."""
        from server.routes.org_models import GitOrgClaimRequest

        for provider in [
            'github',
            'GitLab',
            'BITBUCKET',
            'BITBUCKET_DATA_CENTER',
            'AZURE_DEVOPS',
        ]:
            req = GitOrgClaimRequest(provider=provider, git_organization='test-org')
            assert req.provider == provider.lower().strip()

    def test_invalid_provider_is_rejected(self):
        """An unsupported provider raises a validation error."""
        from pydantic import ValidationError

        from server.routes.org_models import GitOrgClaimRequest

        with pytest.raises(ValidationError, match='Invalid provider'):
            GitOrgClaimRequest(provider='forgejo', git_organization='test-org')

    def test_empty_git_organization_is_rejected(self):
        """An empty git_organization raises a validation error."""
        from pydantic import ValidationError

        from server.routes.org_models import GitOrgClaimRequest

        with pytest.raises(ValidationError, match='git_organization must not be empty'):
            GitOrgClaimRequest(provider='github', git_organization='   ')

    def test_git_organization_is_normalized_to_lowercase(self):
        """git_organization is lowercased to prevent case-sensitive duplicates."""
        from server.routes.org_models import GitOrgClaimRequest

        req = GitOrgClaimRequest(provider='github', git_organization='OpenHands')
        assert req.git_organization == 'openhands'


# =============================================================================
# Integration tests — TestClient with real HTTP, auth, and Pydantic validation
# =============================================================================


@pytest.fixture
def mock_app():
    """FastAPI app with org routes and mocked user authentication."""
    app = FastAPI()
    app.include_router(org_router)

    app.dependency_overrides[get_user_id] = lambda: TEST_USER_ID
    return app


@pytest.fixture
def mock_owner_role():
    role = MagicMock()
    role.name = 'owner'
    return role


@pytest.fixture
def mock_member_role():
    role = MagicMock()
    role.name = 'member'
    return role


class TestGitClaimsAuthorization:
    """Integration tests verifying authorization through the real HTTP cycle."""

    def test_non_member_gets_403_on_get(self, mock_app):
        """
        GIVEN: A user who is not a member of the target organization
        WHEN: GET /api/organizations/{org_id}/git-claims via HTTP
        THEN: 403 is returned by require_permission
        """
        org_id = uuid.uuid4()

        with patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=None),
        ):
            client = TestClient(mock_app)
            response = client.get(f'/api/organizations/{org_id}/git-claims')

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'not a member' in response.json()['detail']

    def test_member_without_permission_gets_403_on_post(
        self, mock_app, mock_member_role
    ):
        """
        GIVEN: A user with member role (lacks MANAGE_ORG_CLAIMS)
        WHEN: POST /api/organizations/{org_id}/git-claims via HTTP
        THEN: 403 is returned by require_permission
        """
        org_id = uuid.uuid4()

        with patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=mock_member_role),
        ):
            client = TestClient(mock_app)
            response = client.post(
                f'/api/organizations/{org_id}/git-claims',
                json={'provider': 'github', 'git_organization': 'SomeOrg'},
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'manage_org_claims' in response.json()['detail']

    def test_member_without_permission_gets_403_on_delete(
        self, mock_app, mock_member_role
    ):
        """
        GIVEN: A user with member role (lacks MANAGE_ORG_CLAIMS)
        WHEN: DELETE /api/organizations/{org_id}/git-claims/{claim_id} via HTTP
        THEN: 403 is returned by require_permission
        """
        org_id = uuid.uuid4()
        claim_id = uuid.uuid4()

        with patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=mock_member_role),
        ):
            client = TestClient(mock_app)
            response = client.delete(
                f'/api/organizations/{org_id}/git-claims/{claim_id}'
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'manage_org_claims' in response.json()['detail']


class TestGitClaimsHTTPIntegration:
    """Integration tests for the full request/response cycle via TestClient."""

    def test_post_claim_with_invalid_provider_returns_422(
        self, mock_app, mock_owner_role
    ):
        """
        GIVEN: A request with an unsupported provider
        WHEN: POST /api/organizations/{org_id}/git-claims via HTTP
        THEN: 422 is returned by Pydantic validation
        """
        org_id = uuid.uuid4()

        with patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=mock_owner_role),
        ):
            client = TestClient(mock_app)
            response = client.post(
                f'/api/organizations/{org_id}/git-claims',
                json={'provider': 'forgejo', 'git_organization': 'test'},
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_post_claim_success_returns_201(self, mock_app, mock_owner_role):
        """
        GIVEN: A valid claim request by an authorized admin/owner
        WHEN: POST /api/organizations/{org_id}/git-claims via HTTP
        THEN: 201 is returned with the claim details
        """
        org_id = uuid.uuid4()
        mock_claim = MagicMock(spec=OrgGitClaim)
        mock_claim.id = uuid.uuid4()
        mock_claim.org_id = org_id
        mock_claim.provider = 'github'
        mock_claim.git_organization = 'openhands'
        mock_claim.claimed_by = uuid.UUID(TEST_USER_ID)
        mock_claim.claimed_at = datetime(2026, 4, 1, 12, 0, 0)

        with (
            patch(
                'server.auth.authorization.get_user_org_role',
                AsyncMock(return_value=mock_owner_role),
            ),
            patch(
                'server.routes.orgs.OrgGitClaimStore.get_claim_by_provider_and_git_org',
                AsyncMock(return_value=None),
            ),
            patch(
                'server.routes.orgs.OrgGitClaimStore.create_claim',
                AsyncMock(return_value=mock_claim),
            ),
        ):
            client = TestClient(mock_app)
            response = client.post(
                f'/api/organizations/{org_id}/git-claims',
                json={'provider': 'github', 'git_organization': 'OpenHands'},
            )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data['org_id'] == str(org_id)
        assert data['provider'] == 'github'
        assert data['git_organization'] == 'openhands'

    def test_delete_claim_success_returns_200(self, mock_app, mock_owner_role):
        """
        GIVEN: A valid disconnect request by an authorized admin/owner
        WHEN: DELETE /api/organizations/{org_id}/git-claims/{claim_id} via HTTP
        THEN: 200 is returned with a success message
        """
        org_id = uuid.uuid4()
        claim_id = uuid.uuid4()
        mock_claim = MagicMock(spec=OrgGitClaim)
        mock_claim.id = claim_id
        mock_claim.provider = 'github'
        mock_claim.git_organization = 'openhands'

        with (
            patch(
                'server.auth.authorization.get_user_org_role',
                AsyncMock(return_value=mock_owner_role),
            ),
            patch(
                'server.routes.orgs.OrgGitClaimStore.delete_claim',
                AsyncMock(return_value=mock_claim),
            ),
        ):
            client = TestClient(mock_app)
            response = client.delete(
                f'/api/organizations/{org_id}/git-claims/{claim_id}'
            )

        assert response.status_code == status.HTTP_200_OK
        assert (
            response.json()['message'] == 'Git organization claim removed successfully'
        )

    def test_get_claims_success_returns_200(self, mock_app, mock_owner_role):
        """
        GIVEN: An authorized user requests claims for their organization
        WHEN: GET /api/organizations/{org_id}/git-claims via HTTP
        THEN: 200 is returned with the list of claims
        """
        org_id = uuid.uuid4()
        mock_claim = MagicMock(spec=OrgGitClaim)
        mock_claim.id = uuid.uuid4()
        mock_claim.org_id = org_id
        mock_claim.provider = 'github'
        mock_claim.git_organization = 'openhands'
        mock_claim.claimed_by = uuid.UUID(TEST_USER_ID)
        mock_claim.claimed_at = datetime(2026, 4, 1, 12, 0, 0)

        with (
            patch(
                'server.auth.authorization.get_user_org_role',
                AsyncMock(return_value=mock_owner_role),
            ),
            patch(
                'server.routes.orgs.OrgGitClaimStore.get_claims_by_org_id',
                AsyncMock(return_value=[mock_claim]),
            ),
        ):
            client = TestClient(mock_app)
            response = client.get(f'/api/organizations/{org_id}/git-claims')

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]['provider'] == 'github'
        assert data[0]['git_organization'] == 'openhands'
