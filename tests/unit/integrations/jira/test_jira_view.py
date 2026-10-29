"""
Tests for Jira view classes and factory.
"""

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.jira.jira_payload import (
    JiraEventType,
    JiraPayloadError,
    JiraPayloadParser,
    JiraPayloadSkipped,
    JiraPayloadSuccess,
)
from integrations.jira.jira_types import (
    RepositorySelectionError,
    StartingConvoException,
)
from integrations.jira.jira_view import (
    JiraFactory,
    JiraNewConversationView,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationStartTaskStatus,
)
from openhands.app_server.integrations.service_types import ProviderType, Repository


class TestJiraNewConversationView:
    """Tests for JiraNewConversationView"""

    @pytest.mark.asyncio
    async def test_get_issue_details_success(
        self, new_conversation_view, sample_jira_workspace
    ):
        """Test successful issue details retrieval."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'fields': {'summary': 'Test Issue', 'description': 'Test description'}
        }
        mock_response.raise_for_status = MagicMock()

        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            title, description = await new_conversation_view.get_issue_details()

            assert title == 'Test Issue'
            assert description == 'Test description'

    @pytest.mark.asyncio
    async def test_get_issue_details_cached(self, new_conversation_view):
        """Test issue details are cached after first call."""
        new_conversation_view._issue_title = 'Cached Title'
        new_conversation_view._issue_description = 'Cached Description'

        title, description = await new_conversation_view.get_issue_details()

        assert title == 'Cached Title'
        assert description == 'Cached Description'

    @pytest.mark.asyncio
    async def test_get_issue_details_no_title(self, new_conversation_view):
        """Test issue details with no title raises error."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'fields': {'summary': '', 'description': 'Test description'}
        }
        mock_response.raise_for_status = MagicMock()

        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(StartingConvoException, match='does not have a title'):
                await new_conversation_view.get_issue_details()

    @pytest.mark.asyncio
    async def test_get_instructions(self, new_conversation_view, mock_jinja_env):
        """Test _get_instructions method fetches issue details."""
        new_conversation_view._issue_title = 'Test Issue'
        new_conversation_view._issue_description = 'This is a test issue'

        instructions, user_msg = await new_conversation_view._get_instructions(
            mock_jinja_env
        )

        assert instructions == 'Test Jira instructions template'
        assert 'TEST-123' in user_msg
        assert 'Test Issue' in user_msg

    @pytest.mark.asyncio
    @patch('integrations.jira.jira_view.get_app_conversation_service')
    @patch('integrations.jira.jira_view.integration_store')
    async def test_create_or_update_conversation_no_repo(
        self,
        mock_store,
        mock_get_service,
        new_conversation_view,
        mock_jinja_env,
    ):
        """Test conversation creation without a selected repository."""
        new_conversation_view.selected_repo = None
        new_conversation_view._issue_title = 'Test Issue'
        new_conversation_view._issue_description = 'Test description'
        mock_store.create_conversation = AsyncMock()
        captured_requests = []

        async def mock_start_generator(request):
            captured_requests.append(request)
            yield MagicMock(status=AppConversationStartTaskStatus.READY)

        mock_service = MagicMock()
        mock_service.start_app_conversation = mock_start_generator
        mock_get_service.return_value.__aenter__.return_value = mock_service

        result = await new_conversation_view.create_or_update_conversation(
            mock_jinja_env
        )

        assert result
        assert len(captured_requests) == 1
        assert captured_requests[0].selected_repository is None
        assert captured_requests[0].git_provider is None
        mock_store.create_conversation.assert_awaited_once()

    @pytest.mark.asyncio
    @patch('integrations.jira.jira_view.get_app_conversation_service')
    @patch('integrations.jira.jira_view.integration_store')
    async def test_create_or_update_conversation_uses_view_git_provider(
        self,
        mock_store,
        mock_get_service,
        new_conversation_view,
        mock_jinja_env,
    ):
        """The start request carries the verified provider, not hardcoded GitHub."""
        new_conversation_view.git_provider = ProviderType.GITLAB
        new_conversation_view._issue_title = 'Test Issue'
        new_conversation_view._issue_description = 'Test description'
        mock_store.create_conversation = AsyncMock()
        captured_requests = []

        async def mock_start_generator(request):
            captured_requests.append(request)
            yield MagicMock(status=AppConversationStartTaskStatus.READY)

        mock_service = MagicMock()
        mock_service.start_app_conversation = mock_start_generator
        mock_get_service.return_value.__aenter__.return_value = mock_service

        result = await new_conversation_view.create_or_update_conversation(
            mock_jinja_env
        )

        assert result
        assert len(captured_requests) == 1
        assert captured_requests[0].selected_repository == 'test/repo1'
        assert captured_requests[0].git_provider == ProviderType.GITLAB

    def test_get_response_msg(self, new_conversation_view):
        """Test get_response_msg method"""
        response = new_conversation_view.get_response_msg()

        assert response.startswith("I'm on it in test/repo1!")
        assert 'Test User' in response
        assert 'track my progress here' in response
        assert 'conv-123' in response
        assert 'started without a repository' not in response

    def test_get_response_msg_without_repo(self, new_conversation_view):
        new_conversation_view.selected_repo = None

        response = new_conversation_view.get_response_msg()

        assert 'Note: This conversation started without a repository.' in response
        assert response.startswith("I'm on it!")

    def test_get_response_msg_names_every_repository(self, new_conversation_view):
        new_conversation_view.additional_repos = [
            _repo('test/repo2'),
            _repo('test/repo3'),
        ]
        new_conversation_view.inaccessible_repos = ['test/missing']
        new_conversation_view.skipped_repos = ['test/eleventh']

        response = new_conversation_view.get_response_msg()

        assert response.startswith(
            "I'm on it in test/repo1, together with test/repo2 and test/repo3!"
        )
        assert 'track my progress here' in response
        assert 'Could not access: test/missing.' in response
        assert (
            'Not included (a conversation can have at most 10 repositories): '
            'test/eleventh.'
        ) in response
        assert 'without a repository' not in response

    def test_get_response_msg_single_repository_has_no_extra_notes(
        self, new_conversation_view
    ):
        response = new_conversation_view.get_response_msg()

        assert 'together with' not in response
        assert 'Could not access' not in response
        assert 'Not included' not in response

    @pytest.mark.asyncio
    @patch('integrations.jira.jira_view.get_app_conversation_service')
    @patch('integrations.jira.jira_view.integration_store')
    async def test_create_conversation_single_repository_sends_no_additional(
        self, mock_store, mock_get_service, new_conversation_view, mock_jinja_env
    ):
        """One repository keeps the single-repository start request."""
        new_conversation_view.git_provider = ProviderType.GITHUB
        request = await _capture_start_request(
            mock_store, mock_get_service, new_conversation_view, mock_jinja_env
        )

        assert request.selected_repository == 'test/repo1'
        assert request.git_provider == ProviderType.GITHUB
        assert request.additional_repositories is None
        assert request.require_all is False

    @pytest.mark.asyncio
    @patch('integrations.jira.jira_view.get_app_conversation_service')
    @patch('integrations.jira.jira_view.integration_store')
    async def test_create_conversation_with_additional_repositories(
        self, mock_store, mock_get_service, new_conversation_view, mock_jinja_env
    ):
        """Every verified repository after the first is passed with its own
        provider; a failed clone must not stop the start."""
        new_conversation_view.git_provider = ProviderType.GITHUB
        new_conversation_view.additional_repos = [
            _repo('test/repo2'),
            _repo('group/repo3', ProviderType.GITLAB),
        ]
        request = await _capture_start_request(
            mock_store, mock_get_service, new_conversation_view, mock_jinja_env
        )

        assert request.selected_repository == 'test/repo1'
        assert request.git_provider == ProviderType.GITHUB
        assert [
            (r.repository, r.git_provider, r.branch)
            for r in request.additional_repositories
        ] == [
            ('test/repo2', ProviderType.GITHUB, None),
            ('group/repo3', ProviderType.GITLAB, None),
        ]
        assert request.require_all is False


def _repo(full_name: str, provider: ProviderType = ProviderType.GITHUB) -> Repository:
    return Repository(
        id=full_name,
        full_name=full_name,
        stargazers_count=0,
        git_provider=provider,
        is_public=False,
    )


async def _capture_start_request(mock_store, mock_get_service, view, jinja_env):
    view._issue_title = 'Test Issue'
    view._issue_description = 'Test description'
    mock_store.create_conversation = AsyncMock()
    captured_requests = []

    async def mock_start_generator(request):
        captured_requests.append(request)
        yield MagicMock(status=AppConversationStartTaskStatus.READY)

    mock_service = MagicMock()
    mock_service.start_app_conversation = mock_start_generator
    mock_get_service.return_value.__aenter__.return_value = mock_service

    with patch.object(view, '_get_resolved_org_id', new_callable=AsyncMock):
        await view.create_or_update_conversation(jinja_env)

    assert len(captured_requests) == 1
    return captured_requests[0]


class TestJiraFactoryRepositoryMentions:
    """Mention handling with the real parser and a fake provider lookup."""

    async def _create_view(
        self,
        payload,
        workspace,
        user,
        user_auth,
        *,
        description: str = 'Test description',
        accessible: dict[str, Repository],
    ):
        """Create a view; mentions whose lowercase name is in accessible verify."""

        async def verify(name):
            if name.lower() in accessible:
                return accessible[name.lower()]
            raise Exception(f'not found: {name}')

        handler = MagicMock()
        handler.verify_repo_provider = AsyncMock(side_effect=verify)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'fields': {'summary': 'Test Issue', 'description': description}
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch('httpx.AsyncClient') as mock_client,
            patch(
                'integrations.jira.jira_view.JiraFactory._create_provider_handler',
                new=AsyncMock(return_value=handler),
            ),
        ):
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            view = await JiraFactory.create_view(
                payload=payload,
                workspace=workspace,
                user=user,
                user_auth=user_auth,
                decrypted_api_key='test_api_key',
            )
        return view, handler

    @staticmethod
    def _accessible(*names: str) -> dict[str, Repository]:
        return {n.lower(): _repo(n) for n in names}

    @pytest.fixture
    def args(
        self,
        sample_webhook_payload,
        sample_jira_workspace,
        sample_jira_user,
        sample_user_auth,
    ):
        return (
            sample_webhook_payload,
            sample_jira_workspace,
            sample_jira_user,
            sample_user_auth,
        )

    @staticmethod
    def _with_comment(args, comment):
        payload, *rest = args
        return (replace(payload, comment_body=comment), *rest)

    @pytest.mark.asyncio
    async def test_single_mention_unchanged(self, args):
        view, _ = await self._create_view(
            *self._with_comment(args, 'Fix acme/api @openhands'),
            accessible=self._accessible('acme/api'),
        )

        assert view.selected_repo == 'acme/api'
        assert view.git_provider == ProviderType.GITHUB
        assert view.additional_repos == []
        assert view.inaccessible_repos == []
        assert view.skipped_repos == []

    @pytest.mark.asyncio
    async def test_several_mentions_start_one_conversation(self, args):
        view, _ = await self._create_view(
            *self._with_comment(args, 'Change acme/api and acme/web @openhands'),
            accessible=self._accessible('acme/api', 'acme/web'),
        )

        assert view.selected_repo == 'acme/api'
        assert [r.full_name for r in view.additional_repos] == ['acme/web']
        assert view.inaccessible_repos == []

    @pytest.mark.asyncio
    async def test_unverified_comment_mention_is_reported_not_fatal(self, args):
        view, _ = await self._create_view(
            *self._with_comment(
                args, 'Change acme/api, acme/missing and acme/web @openhands'
            ),
            accessible=self._accessible('acme/api', 'acme/web'),
        )

        assert view.selected_repo == 'acme/api'
        assert [r.full_name for r in view.additional_repos] == ['acme/web']
        assert view.inaccessible_repos == ['acme/missing']
        assert 'Could not access: acme/missing.' in view.get_response_msg()

    @pytest.mark.asyncio
    async def test_zero_mentions_start_without_repository(self, args):
        view, handler = await self._create_view(
            *self._with_comment(args, 'Please fix this @openhands'),
            description='No repository named here.',
            accessible={},
        )

        assert view.selected_repo is None
        assert view.git_provider is None
        assert view.additional_repos == []
        handler.verify_repo_provider.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_more_than_ten_mentions_are_capped(self, args):
        names = [f'acme/repo{i}' for i in range(12)]
        view, _ = await self._create_view(
            *self._with_comment(args, ' '.join(names) + ' @openhands'),
            accessible=self._accessible(*names),
        )

        assert view.selected_repo == 'acme/repo0'
        assert [r.full_name for r in view.additional_repos] == names[1:10]
        assert view.skipped_repos == names[10:]

    @pytest.mark.asyncio
    async def test_comment_mentions_come_before_description(self, args):
        view, _ = await self._create_view(
            *self._with_comment(args, 'Work in acme/api @openhands'),
            description='Originally reported against acme/web.',
            accessible=self._accessible('acme/api', 'acme/web'),
        )

        assert view.selected_repo == 'acme/api'
        assert [r.full_name for r in view.additional_repos] == ['acme/web']

    @pytest.mark.asyncio
    async def test_unverified_description_text_is_ignored(self, args):
        view, _ = await self._create_view(
            *self._with_comment(args, 'Please fix this @openhands'),
            description=(
                'See https://acme.atlassian.net/browse/ABC-1 for the client/server '
                'split; the code is in acme/web.'
            ),
            accessible=self._accessible('acme/web'),
        )

        assert view.selected_repo == 'acme/web'
        assert view.additional_repos == []
        assert view.inaccessible_repos == []
        assert 'Could not access' not in view.get_response_msg()

    @pytest.mark.asyncio
    async def test_variants_of_one_repository_count_once(self, args):
        repo = _repo('acme/api')
        view, _ = await self._create_view(
            *self._with_comment(args, 'acme/api @openhands'),
            description='https://github.com/ACME/API',
            accessible={'acme/api': repo},
        )

        assert view.selected_repo == 'acme/api'
        assert view.additional_repos == []

    @pytest.mark.asyncio
    async def test_nothing_verified_does_not_start(self, args):
        with pytest.raises(RepositorySelectionError) as exc_info:
            await self._create_view(
                *self._with_comment(args, 'Change acme/one and acme/two @openhands'),
                description='Also browse/ABC-1',
                accessible={},
            )

        message = str(exc_info.value)
        assert message.startswith(
            'Could not access any of the mentioned repositories: '
            'acme/one, acme/two, browse/ABC-1. No conversation was started.'
        )


class TestJiraFactory:
    """Tests for JiraFactory"""

    @pytest.mark.asyncio
    @patch('integrations.jira.jira_view.JiraFactory._create_provider_handler')
    @patch('integrations.jira.jira_view.infer_repo_from_message')
    async def test_create_view_success(
        self,
        mock_infer_repos,
        mock_create_handler,
        sample_webhook_payload,
        sample_user_auth,
        sample_jira_user,
        sample_jira_workspace,
        sample_repositories,
    ):
        """Test factory creating view with repo selection."""
        # Setup mock provider handler
        mock_handler = MagicMock()
        mock_handler.verify_repo_provider = AsyncMock(
            return_value=sample_repositories[0]
        )
        mock_create_handler.return_value = mock_handler

        # Mock repo inference to return a repo name
        mock_infer_repos.return_value = ['test/repo1']

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'fields': {'summary': 'Test Issue', 'description': 'Test description'}
        }
        mock_response.raise_for_status = MagicMock()

        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            view = await JiraFactory.create_view(
                payload=sample_webhook_payload,
                workspace=sample_jira_workspace,
                user=sample_jira_user,
                user_auth=sample_user_auth,
                decrypted_api_key='test_api_key',
            )

            assert isinstance(view, JiraNewConversationView)
            assert view.selected_repo == 'test/repo1'
            assert view.git_provider == ProviderType.GITHUB
            mock_handler.verify_repo_provider.assert_called_once_with('test/repo1')

    @pytest.mark.asyncio
    @patch('integrations.jira.jira_view.JiraFactory._create_provider_handler')
    @patch('integrations.jira.jira_view.infer_repo_from_message')
    async def test_create_view_gitlab_repo(
        self,
        mock_infer_repos,
        mock_create_handler,
        sample_webhook_payload,
        sample_user_auth,
        sample_jira_user,
        sample_jira_workspace,
    ):
        """A verified GitLab repo yields a GitLab provider on the view."""
        gitlab_repo = Repository(
            id='3',
            full_name='group/gitlab-repo',
            stargazers_count=0,
            git_provider=ProviderType.GITLAB,
            is_public=False,
        )
        mock_handler = MagicMock()
        mock_handler.verify_repo_provider = AsyncMock(return_value=gitlab_repo)
        mock_create_handler.return_value = mock_handler
        mock_infer_repos.return_value = ['group/gitlab-repo']

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'fields': {'summary': 'Test Issue', 'description': 'Test description'}
        }
        mock_response.raise_for_status = MagicMock()

        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            view = await JiraFactory.create_view(
                payload=sample_webhook_payload,
                workspace=sample_jira_workspace,
                user=sample_jira_user,
                user_auth=sample_user_auth,
                decrypted_api_key='test_api_key',
            )

            assert view.selected_repo == 'group/gitlab-repo'
            assert view.git_provider == ProviderType.GITLAB

    @pytest.mark.asyncio
    @patch('integrations.jira.jira_view.JiraFactory._create_provider_handler')
    @patch('integrations.jira.jira_view.infer_repo_from_message')
    async def test_create_view_no_repo_in_text(
        self,
        mock_infer_repos,
        mock_create_handler,
        sample_webhook_payload,
        sample_user_auth,
        sample_jira_user,
        sample_jira_workspace,
    ):
        """Test factory creates a view without a selected repository."""
        mock_infer_repos.return_value = []

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'fields': {'summary': 'Test Issue', 'description': 'Test description'}
        }
        mock_response.raise_for_status = MagicMock()

        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            view = await JiraFactory.create_view(
                payload=sample_webhook_payload,
                workspace=sample_jira_workspace,
                user=sample_jira_user,
                user_auth=sample_user_auth,
                decrypted_api_key='test_api_key',
            )

        assert view.selected_repo is None
        mock_create_handler.assert_not_awaited()

    @pytest.mark.asyncio
    @patch('integrations.jira.jira_view.JiraFactory._create_provider_handler')
    @patch('integrations.jira.jira_view.infer_repo_from_message')
    async def test_create_view_repo_verification_fails(
        self,
        mock_infer_repos,
        mock_create_handler,
        sample_webhook_payload,
        sample_user_auth,
        sample_jira_user,
        sample_jira_workspace,
    ):
        """Test factory raises error when repo verification fails."""
        mock_handler = MagicMock()
        mock_handler.verify_repo_provider = AsyncMock(
            side_effect=Exception('Repository not found')
        )
        mock_create_handler.return_value = mock_handler

        # Repos found in text but verification fails
        mock_infer_repos.return_value = ['test/repo1', 'test/repo2']

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'fields': {'summary': 'Test Issue', 'description': 'Test description'}
        }
        mock_response.raise_for_status = MagicMock()

        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(
                RepositorySelectionError,
                match='Could not access any of the mentioned repositories',
            ):
                await JiraFactory.create_view(
                    payload=sample_webhook_payload,
                    workspace=sample_jira_workspace,
                    user=sample_jira_user,
                    user_auth=sample_user_auth,
                    decrypted_api_key='test_api_key',
                )

    @pytest.mark.asyncio
    @patch('integrations.jira.jira_view.JiraFactory._create_provider_handler')
    @patch('integrations.jira.jira_view.infer_repo_from_message')
    async def test_create_view_multiple_repos_verified(
        self,
        mock_infer_repos,
        mock_create_handler,
        sample_webhook_payload,
        sample_user_auth,
        sample_jira_user,
        sample_jira_workspace,
        sample_repositories,
    ):
        """Several verified repos start one conversation: first is primary."""
        mock_handler = MagicMock()
        # Both repos verify successfully
        mock_handler.verify_repo_provider = AsyncMock(
            side_effect=[sample_repositories[0], sample_repositories[1]]
        )
        mock_create_handler.return_value = mock_handler

        # Multiple repos found in text
        mock_infer_repos.return_value = ['test/repo1', 'test/repo2']

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'fields': {'summary': 'Test Issue', 'description': 'Test description'}
        }
        mock_response.raise_for_status = MagicMock()

        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            view = await JiraFactory.create_view(
                payload=sample_webhook_payload,
                workspace=sample_jira_workspace,
                user=sample_jira_user,
                user_auth=sample_user_auth,
                decrypted_api_key='test_api_key',
            )

        assert view.selected_repo == 'test/repo1'
        assert [r.full_name for r in view.additional_repos] == ['test/repo2']

    @pytest.mark.asyncio
    @patch('integrations.jira.jira_view.JiraFactory._create_provider_handler')
    @patch('integrations.jira.jira_view.infer_repo_from_message')
    async def test_create_view_no_provider(
        self,
        mock_infer_repos,
        mock_create_handler,
        sample_webhook_payload,
        sample_user_auth,
        sample_jira_user,
        sample_jira_workspace,
    ):
        """Test factory raises error for a mentioned repo without a provider."""
        mock_infer_repos.return_value = ['test/repo1']
        mock_create_handler.return_value = None

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'fields': {'summary': 'Test Issue', 'description': 'Test description'}
        }
        mock_response.raise_for_status = MagicMock()

        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(
                RepositorySelectionError, match='No Git provider connected'
            ):
                await JiraFactory.create_view(
                    payload=sample_webhook_payload,
                    workspace=sample_jira_workspace,
                    user=sample_jira_user,
                    user_auth=sample_user_auth,
                    decrypted_api_key='test_api_key',
                )


class TestJiraPayloadParser:
    """Tests for JiraPayloadParser"""

    @pytest.fixture
    def parser(self):
        """Create a parser for testing."""
        return JiraPayloadParser(oh_label='openhands', inline_oh_label='@openhands')

    def test_parse_label_event_success(
        self, parser, sample_issue_update_webhook_payload
    ):
        """Test parsing label event."""
        result = parser.parse(sample_issue_update_webhook_payload)

        assert isinstance(result, JiraPayloadSuccess)
        assert result.payload.event_type == JiraEventType.LABELED_TICKET
        assert result.payload.issue_key == 'PROJ-123'

    def test_parse_comment_event_success(self, parser, sample_comment_webhook_payload):
        """Test parsing comment event."""
        result = parser.parse(sample_comment_webhook_payload)

        assert isinstance(result, JiraPayloadSuccess)
        assert result.payload.event_type == JiraEventType.COMMENT_MENTION
        assert result.payload.issue_key == 'TEST-123'
        assert '@openhands' in result.payload.comment_body

    def test_parse_unknown_event_skipped(self, parser):
        """Test unknown event is skipped."""
        payload = {'webhookEvent': 'unknown_event'}
        result = parser.parse(payload)

        assert isinstance(result, JiraPayloadSkipped)
        assert 'Unhandled webhook event type' in result.skip_reason

    def test_parse_label_event_wrong_label_skipped(self, parser):
        """Test label event without OH label is skipped."""
        payload = {
            'webhookEvent': 'jira:issue_updated',
            'changelog': {'items': [{'field': 'labels', 'toString': 'other-label'}]},
        }
        result = parser.parse(payload)

        assert isinstance(result, JiraPayloadSkipped)
        assert 'does not contain' in result.skip_reason

    def test_parse_comment_event_no_mention_skipped(self, parser):
        """Test comment without mention is skipped."""
        payload = {
            'webhookEvent': 'comment_created',
            'comment': {
                'body': 'Regular comment',
                'author': {'emailAddress': 'test@test.com'},
            },
        }
        result = parser.parse(payload)

        assert isinstance(result, JiraPayloadSkipped)
        assert 'does not mention' in result.skip_reason

    def test_parse_missing_fields_error(self, parser):
        """Test missing required fields returns error."""
        payload = {
            'webhookEvent': 'jira:issue_updated',
            'changelog': {'items': [{'field': 'labels', 'toString': 'openhands'}]},
            'issue': {'id': '123'},  # Missing key
            'user': {'emailAddress': 'test@test.com'},  # Missing other fields
        }
        result = parser.parse(payload)

        assert isinstance(result, JiraPayloadError)
        assert 'Missing required fields' in result.error


class TestJiraPayloadParserStagingLabels:
    """Tests for JiraPayloadParser with staging labels."""

    @pytest.fixture
    def staging_parser(self):
        """Create a parser with staging labels."""
        return JiraPayloadParser(
            oh_label='openhands-exp', inline_oh_label='@openhands-exp'
        )

    def test_parse_staging_label(self, staging_parser):
        """Test parsing with staging label."""
        payload = {
            'webhookEvent': 'jira:issue_updated',
            'changelog': {'items': [{'field': 'labels', 'toString': 'openhands-exp'}]},
            'issue': {
                'id': '123',
                'key': 'TEST-1',
                'self': 'https://test.atlassian.net/rest/api/2/issue/123',
            },
            'user': {
                'emailAddress': 'test@test.com',
                'displayName': 'Test',
                'accountId': 'acc123',
                'self': 'https://test.atlassian.net/rest/api/2/user',
            },
        }
        result = staging_parser.parse(payload)

        assert isinstance(result, JiraPayloadSuccess)
        assert result.payload.event_type == JiraEventType.LABELED_TICKET

    def test_parse_prod_label_in_staging_skipped(self, staging_parser):
        """Test prod label is skipped in staging environment."""
        payload = {
            'webhookEvent': 'jira:issue_updated',
            'changelog': {'items': [{'field': 'labels', 'toString': 'openhands'}]},
        }
        result = staging_parser.parse(payload)

        assert isinstance(result, JiraPayloadSkipped)


class TestJiraV1Conversation:
    """Tests for V1 conversation creation and callback processor registration."""

    @pytest.mark.asyncio
    async def test_initialize_conversation_generates_conversation_id(
        self, new_conversation_view
    ):
        """Test that _initialize_conversation generates a new conversation ID."""
        new_conversation_view.conversation_id = ''

        with patch.object(
            new_conversation_view, '_get_resolved_org_id', new_callable=AsyncMock
        ) as mock_get_org:
            mock_get_org.return_value = None

            conversation_id = await new_conversation_view._initialize_conversation()

            # Conversation ID should be generated
            assert new_conversation_view.conversation_id != ''
            assert len(new_conversation_view.conversation_id) == 32  # UUID hex format
            assert conversation_id.hex == new_conversation_view.conversation_id
            mock_get_org.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_conversation_sets_resolved_org(
        self, new_conversation_view
    ):
        """Test that _initialize_conversation sets resolved_org_id."""
        from uuid import UUID

        test_org_id = UUID('12345678-1234-5678-1234-567812345678')

        with patch.object(
            new_conversation_view, '_get_resolved_org_id', new_callable=AsyncMock
        ) as mock_get_org:
            mock_get_org.return_value = test_org_id

            await new_conversation_view._initialize_conversation()

            assert new_conversation_view.resolved_org_id == test_org_id

    @pytest.mark.asyncio
    async def test_get_resolved_org_id_without_repo_skips_provider_lookup(
        self, new_conversation_view
    ):
        new_conversation_view.selected_repo = None

        resolved_org_id = await new_conversation_view._get_resolved_org_id()

        assert resolved_org_id is None
        new_conversation_view.saas_user_auth.get_provider_tokens.assert_not_awaited()

    def test_create_jira_v1_callback_processor(
        self, new_conversation_view, sample_jira_workspace
    ):
        """Test that _create_jira_v1_callback_processor creates correctly configured processor."""
        from integrations.jira.jira_v1_callback_processor import JiraV1CallbackProcessor

        processor = new_conversation_view._create_jira_v1_callback_processor()

        assert isinstance(processor, JiraV1CallbackProcessor)
        assert processor.svc_acc_email == sample_jira_workspace.svc_acc_email
        assert processor.decrypted_api_key == 'decrypted_key'
        assert processor.issue_key == 'TEST-123'
        assert processor.jira_cloud_id == sample_jira_workspace.jira_cloud_id

    @pytest.mark.asyncio
    async def test_get_v1_initial_user_message(
        self, new_conversation_view, mock_jinja_env
    ):
        """Test _get_v1_initial_user_message renders the template correctly."""
        new_conversation_view._issue_title = 'Test Bug'
        new_conversation_view._issue_description = 'Description of the bug'

        message = await new_conversation_view._get_v1_initial_user_message(
            mock_jinja_env
        )

        assert 'TEST-123' in message
        assert 'Test Bug' in message

    @pytest.mark.asyncio
    @patch('integrations.jira.jira_view.get_app_conversation_service')
    @patch('integrations.jira.jira_view.integration_store')
    async def test_create_or_update_conversation_v1_flow(
        self,
        mock_store,
        mock_get_service,
        new_conversation_view,
        mock_jinja_env,
    ):
        """Test create_or_update_conversation creates V1 conversation correctly."""
        from unittest.mock import AsyncMock, MagicMock
        from uuid import UUID

        from openhands.app_server.app_conversation.app_conversation_models import (
            AppConversationStartTaskStatus,
        )

        # Setup mocks
        mock_store.create_conversation = AsyncMock()

        # Mock the app conversation service
        mock_service = AsyncMock()

        async def mock_start_generator(*args, **kwargs):
            yield MagicMock(status=AppConversationStartTaskStatus.WORKING)
            yield MagicMock(status=AppConversationStartTaskStatus.READY)

        mock_service.start_app_conversation = mock_start_generator
        mock_get_service.return_value.__aenter__.return_value = mock_service

        # Set issue details to avoid fetch
        new_conversation_view._issue_title = 'Test Issue'
        new_conversation_view._issue_description = 'Test description'

        with patch.object(
            new_conversation_view, '_get_resolved_org_id', new_callable=AsyncMock
        ) as mock_get_org:
            mock_get_org.return_value = UUID('12345678-1234-5678-1234-567812345678')

            result = await new_conversation_view.create_or_update_conversation(
                mock_jinja_env
            )

            # Verify conversation was created
            assert result is not None
            mock_store.create_conversation.assert_called_once()

    @pytest.mark.asyncio
    @patch('integrations.jira.jira_view.get_app_conversation_service')
    @patch('integrations.jira.jira_view.integration_store')
    async def test_create_or_update_conversation_handles_error(
        self,
        mock_store,
        mock_get_service,
        new_conversation_view,
        mock_jinja_env,
    ):
        """Test create_or_update_conversation handles V1 errors correctly."""
        from unittest.mock import AsyncMock, MagicMock

        from openhands.app_server.app_conversation.app_conversation_models import (
            AppConversationStartTaskStatus,
        )

        mock_store.create_conversation = AsyncMock()

        # Mock the app conversation service to return error
        mock_service = AsyncMock()

        async def mock_error_generator(*args, **kwargs):
            yield MagicMock(
                status=AppConversationStartTaskStatus.ERROR,
                detail='Sandbox allocation failed',
            )

        mock_service.start_app_conversation = mock_error_generator
        mock_get_service.return_value.__aenter__.return_value = mock_service

        new_conversation_view._issue_title = 'Test Issue'
        new_conversation_view._issue_description = 'Test description'

        with patch.object(
            new_conversation_view, '_get_resolved_org_id', new_callable=AsyncMock
        ) as mock_get_org:
            mock_get_org.return_value = None

            with pytest.raises(RuntimeError, match='Failed to start V1 conversation'):
                await new_conversation_view.create_or_update_conversation(
                    mock_jinja_env
                )
