"""Jira view implementations and factory.

Views are responsible for:
- Holding the webhook payload and auth context
- Lazy-loading issue details from Jira API when needed
- Creating conversations with the selected repositories
"""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import httpx
from jinja2 import Environment

from integrations.jira.jira_payload import JiraWebhookPayload
from integrations.jira.jira_types import (
    JiraViewInterface,
    RepositorySelectionError,
    StartingConvoException,
)
from integrations.jira.jira_v1_callback_processor import (
    JiraV1CallbackProcessor,
)
from integrations.resolver_context import ResolverUserContext
from integrations.resolver_org_router import resolve_org_for_repo
from integrations.utils import (
    CONVERSATION_URL,
    infer_repo_from_message,
)
from openhands.agent_server.models import SendMessageRequest
from openhands.app_server.app_conversation.additional_repositories import (
    MAX_ADDITIONAL_REPOSITORIES,
    AdditionalRepositoryRequest,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationStartRequest,
    AppConversationStartTaskStatus,
    ConversationTrigger,
)
from openhands.app_server.config import get_app_conversation_service
from openhands.app_server.integrations.provider import (
    ProviderHandler,
    ProviderType,
    Repository,
)
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.specifiy_user_context import USER_CONTEXT_ATTR
from openhands.app_server.user_auth.user_auth import UserAuth
from openhands.app_server.utils.http_session import httpx_verify_option
from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.sdk import TextContent
from server.auth.constants import JIRA_HTTP_TIMEOUT
from storage.jira_conversation import JiraConversation
from storage.jira_integration_store import JiraIntegrationStore
from storage.jira_user import JiraUser
from storage.jira_workspace import JiraWorkspace

JIRA_CLOUD_API_URL = 'https://api.atlassian.com/ex/jira'

integration_store = JiraIntegrationStore.get_instance()


@dataclass
class JiraNewConversationView(JiraViewInterface):
    """View for creating a new Jira conversation.

    This view holds the webhook payload directly and lazily fetches
    issue details when needed for rendering templates.
    """

    payload: JiraWebhookPayload
    saas_user_auth: UserAuth
    jira_user: JiraUser
    jira_workspace: JiraWorkspace
    selected_repo: str | None = None
    # Provider of the verified selected_repo (set by factory); repos are not
    # always GitHub.
    git_provider: ProviderType | None = None
    conversation_id: str = ''

    # Lazy-loaded issue details (cached after first fetch)
    _issue_title: str | None = field(default=None, repr=False)
    _issue_description: str | None = field(default=None, repr=False)

    # Decrypted API key (set by factory)
    _decrypted_api_key: str = field(default='', repr=False)

    # Resolved org ID for V1 conversations
    resolved_org_id: UUID | None = None
    additional_repos: list[Repository] = field(default_factory=list)
    inaccessible_repos: list[str] = field(default_factory=list)
    skipped_repos: list[str] = field(default_factory=list)

    async def get_issue_details(self) -> tuple[str, str]:
        """Fetch issue details from Jira API (cached after first call).

        Returns:
            Tuple of (issue_title, issue_description)

        Raises:
            StartingConvoException: If issue details cannot be fetched
        """
        if self._issue_title is not None and self._issue_description is not None:
            return self._issue_title, self._issue_description

        try:
            url = f'{JIRA_CLOUD_API_URL}/{self.jira_workspace.jira_cloud_id}/rest/api/2/issue/{self.payload.issue_key}'
            async with httpx.AsyncClient(
                verify=httpx_verify_option(), timeout=JIRA_HTTP_TIMEOUT
            ) as client:
                response = await client.get(
                    url,
                    auth=(
                        self.jira_workspace.svc_acc_email,
                        self._decrypted_api_key,
                    ),
                )
                response.raise_for_status()
                issue_payload = response.json()

            if not issue_payload:
                raise StartingConvoException(
                    f'Issue {self.payload.issue_key} not found.'
                )

            self._issue_title = issue_payload.get('fields', {}).get('summary', '')
            self._issue_description = (
                issue_payload.get('fields', {}).get('description', '') or ''
            )

            if not self._issue_title:
                raise StartingConvoException(
                    f'Issue {self.payload.issue_key} does not have a title.'
                )

            logger.info(
                '[Jira] Fetched issue details',
                extra={
                    'issue_key': self.payload.issue_key,
                    'has_description': bool(self._issue_description),
                },
            )

            return self._issue_title, self._issue_description

        except httpx.HTTPStatusError as e:
            logger.exception(
                '[Jira] Failed to fetch issue details',
                extra={
                    'issue_key': self.payload.issue_key,
                    'status': e.response.status_code,
                },
                stack_info=True,
            )
            raise StartingConvoException('Failed to fetch issue details: HTTP') from e
        except Exception as e:
            if isinstance(e, StartingConvoException):
                raise
            logger.exception(
                '[Jira] Failed to fetch issue details',
                extra={
                    'issue_key': self.payload.issue_key,
                },
                stack_info=True,
            )
            raise StartingConvoException('Failed to fetch issue details') from e

    async def _get_instructions(self, jinja_env: Environment) -> tuple[str, str]:
        """Get instructions for the conversation.

        This fetches issue details if not already cached.

        Returns:
            Tuple of (system_instructions, user_message)
        """
        issue_title, issue_description = await self.get_issue_details()

        instructions_template = jinja_env.get_template('jira_instructions.j2')
        instructions = instructions_template.render()

        user_msg_template = jinja_env.get_template('jira_new_conversation.j2')
        user_msg = user_msg_template.render(
            issue_key=self.payload.issue_key,
            issue_title=issue_title,
            issue_description=issue_description,
            user_message=self.payload.user_msg,
        )

        return instructions, user_msg

    async def create_or_update_conversation(self, jinja_env: Environment) -> str:
        """Create a new Jira conversation.

        Returns:
            The conversation ID

        Raises:
            StartingConvoException: If conversation creation fails
        """
        jira_conversation = JiraConversation(
            conversation_id=self.conversation_id,
            issue_id=self.payload.issue_id,
            issue_key=self.payload.issue_key,
            jira_user_id=self.jira_user.id,
        )
        await integration_store.create_conversation(jira_conversation)

        conversation_id = await self._initialize_conversation()
        await self._create_v1_conversation(jinja_env, conversation_id)
        return self.conversation_id

    async def _initialize_conversation(self) -> UUID:
        """Initialize conversation and return the conversation ID.

        The JiraConversation mapping is saved to the integration store (above), but
        V1 conversation metadata is managed by the app conversation system, not
        the legacy conversation store.
        """
        logger.info('[Jira]: Initializing V1 conversation')

        conversation_id = uuid4()
        self.conversation_id = conversation_id.hex
        self.resolved_org_id = await self._get_resolved_org_id()

        return conversation_id

    async def _create_v1_conversation(
        self,
        jinja_env: Environment,
        conversation_id: UUID,
    ):
        """Create conversation using the new V1 app conversation system."""
        logger.info('[Jira]: Creating V1 conversation')

        initial_user_text = await self._get_v1_initial_user_message(jinja_env)

        initial_message = SendMessageRequest(
            role='user', content=[TextContent(text=initial_user_text)]
        )

        jira_callback_processor = self._create_jira_v1_callback_processor()

        injector_state = InjectorState()

        start_request = AppConversationStartRequest(
            conversation_id=conversation_id,
            system_message_suffix=None,
            initial_message=initial_message,
            selected_repository=self.selected_repo,
            selected_branch=None,
            git_provider=self.git_provider if self.selected_repo else None,
            # Cloned beside the primary; a failed clone is reported on the
            # conversation and does not stop the start (require_all stays off).
            additional_repositories=[
                AdditionalRepositoryRequest(
                    repository=repo.full_name, git_provider=repo.git_provider
                )
                for repo in self.additional_repos
            ]
            or None,
            title=f'Jira Issue {self.payload.issue_key}: {self._issue_title or "Unknown"}',
            trigger=ConversationTrigger.JIRA,
            processors=[jira_callback_processor],
        )

        jira_user_context = ResolverUserContext(
            saas_user_auth=self.saas_user_auth,
            resolver_org_id=self.resolved_org_id,
        )
        setattr(injector_state, USER_CONTEXT_ATTR, jira_user_context)

        async with get_app_conversation_service(
            injector_state
        ) as app_conversation_service:
            async for task in app_conversation_service.start_app_conversation(
                start_request
            ):
                if task.status == AppConversationStartTaskStatus.ERROR:
                    logger.error(f'Failed to start V1 conversation: {task.detail}')
                    raise RuntimeError(
                        f'Failed to start V1 conversation: {task.detail}'
                    )

    async def _get_v1_initial_user_message(self, jinja_env: Environment) -> str:
        """Build the initial user message for V1 resolver conversations."""
        issue_title, issue_description = await self.get_issue_details()

        user_msg_template = jinja_env.get_template('jira_new_conversation.j2')
        user_msg = user_msg_template.render(
            issue_key=self.payload.issue_key,
            issue_title=issue_title,
            issue_description=issue_description,
            user_message=self.payload.user_msg,
        )

        return user_msg

    def _create_jira_v1_callback_processor(self):
        """Create a V1 callback processor for Jira integration."""
        return JiraV1CallbackProcessor(
            svc_acc_email=self.jira_workspace.svc_acc_email,
            decrypted_api_key=self._decrypted_api_key,
            issue_key=self.payload.issue_key,
            jira_cloud_id=self.jira_workspace.jira_cloud_id,
        )

    async def _get_resolved_org_id(self) -> UUID | None:
        """Resolve the org ID for V1 conversations."""
        selected_repo = self.selected_repo
        if not selected_repo:
            return None

        provider_tokens = await self.saas_user_auth.get_provider_tokens()
        if not provider_tokens:
            return None

        try:
            provider_handler = ProviderHandler(provider_tokens)
            repository = await provider_handler.verify_repo_provider(selected_repo)
            resolved_org_id = await resolve_org_for_repo(
                provider=repository.git_provider.value,
                full_repo_name=selected_repo,
                keycloak_user_id=self.jira_user.keycloak_user_id,
            )
            return resolved_org_id
        except Exception as e:
            logger.warning(f'[Jira] Failed to resolve org for {selected_repo}: {e}')
            return None

    def get_response_msg(self) -> str:
        """Get the response message to send back to Jira."""
        conversation_link = CONVERSATION_URL.format(self.conversation_id)
        # Name the resolved repos so the user can confirm we picked the right ones.
        if self.selected_repo:
            prefix = f"I'm on it in {self.selected_repo}"
            if self.additional_repos:
                prefix += f', together with {_join([r.full_name for r in self.additional_repos])}'
            prefix += '!'
        else:
            prefix = "I'm on it!"
        message = f'{prefix} {self.payload.display_name} can [track my progress here|{conversation_link}].'
        if self.selected_repo is None:
            message += '\n\nNote: This conversation started without a repository.'
        if self.inaccessible_repos:
            message += (
                f'\n\nCould not access: {", ".join(self.inaccessible_repos)}. '
                'Please ensure each repository exists and that the Git account '
                'linked to your OpenHands user can access it.'
            )
        if self.skipped_repos:
            message += (
                f'\n\nNot included (a conversation can have at most '
                f'{MAX_ADDITIONAL_REPOSITORIES + 1} repositories): '
                f'{", ".join(self.skipped_repos)}.'
            )
        return message


def _join(names: list[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    if len(names) < 2:
        return ''.join(names)
    return f'{", ".join(names[:-1])} and {names[-1]}'


class JiraFactory:
    """Factory for creating Jira views.

    The factory is responsible for:
    - Creating the appropriate view type
    - Inferring and selecting the repositories
    - Validating all required data is available

    Repository selection happens here so view creation succeeds either with the
    verified repositories or with no repository when none was mentioned.
    """

    @staticmethod
    async def _create_provider_handler(user_auth: UserAuth) -> ProviderHandler | None:
        """Create a ProviderHandler for the user."""
        provider_tokens = await user_auth.get_provider_tokens()
        if provider_tokens is None:
            return None

        access_token = await user_auth.get_access_token()
        user_id = await user_auth.get_user_id()

        return ProviderHandler(
            provider_tokens=provider_tokens,
            external_auth_token=access_token,
            external_auth_id=user_id,
        )

    @staticmethod
    def _extract_potential_repos(
        issue_key: str,
        issue_title: str,
        issue_description: str,
        user_msg: str,
    ) -> tuple[list[str], list[str]]:
        """Extract potential repository names from issue content.

        Returns all mentions in order, and the subset from the triggering
        comment. Comment mentions come first: they are the user's deliberate
        choice, while the description often links to Jira, Confluence or
        upstream issues that also look like a/b.
        """
        comment_mentions = infer_repo_from_message(user_msg or '')
        issue_mentions = infer_repo_from_message(f'{issue_title}\n{issue_description}')
        potential_repos = list(dict.fromkeys([*comment_mentions, *issue_mentions]))

        if potential_repos:
            logger.info(
                '[Jira] Found potential repositories in issue content',
                extra={'issue_key': issue_key, 'potential_repos': potential_repos},
            )
        else:
            logger.info(
                '[Jira] No repositories mentioned in issue content',
                extra={'issue_key': issue_key},
            )
        return potential_repos, comment_mentions

    @staticmethod
    async def _verify_repos(
        issue_key: str,
        potential_repos: list[str],
        provider_handler: ProviderHandler,
    ) -> tuple[list[Repository], list[str]]:
        """Verify which repos the user has access to.

        Returns the verified repos in mention order and the mentions that could
        not be resolved.
        """
        # Dedupe by canonical full_name: two forms of one repo (case/URL
        # variants, a rename) must not be cloned twice.
        verified: dict[str, Repository] = {}
        unverified: list[str] = []

        for repo_name in potential_repos:
            try:
                repository = await provider_handler.verify_repo_provider(repo_name)
                verified.setdefault(repository.full_name.lower(), repository)
                logger.debug(
                    '[Jira] Repository verification succeeded',
                    extra={'issue_key': issue_key, 'repository': repository.full_name},
                )
            except Exception as e:
                unverified.append(repo_name)
                logger.debug(
                    '[Jira] Repository verification failed',
                    extra={
                        'issue_key': issue_key,
                        'repo_name': repo_name,
                        'error': str(e),
                    },
                )

        return list(verified.values()), unverified

    @staticmethod
    async def _select_repositories(
        view: JiraNewConversationView,
        issue_title: str,
        issue_description: str,
    ) -> None:
        """Infer, verify and set the view's repositories from issue content.

        The first verified mention is the primary repository; the rest are
        cloned beside it. Only mentions from the comment that could not be
        accessed are reported: unverified text from the description is usually
        not a repository at all.

        Raises:
            RepositorySelectionError: If repositories are mentioned but none
                can be used.
        """
        payload = view.payload
        potential_repos, comment_mentions = JiraFactory._extract_potential_repos(
            payload.issue_key, issue_title, issue_description, payload.user_msg
        )
        if not potential_repos:
            return

        provider_handler = await JiraFactory._create_provider_handler(
            view.saas_user_auth
        )
        if not provider_handler:
            raise RepositorySelectionError(
                'No Git provider connected. Please connect a Git provider in OpenHands settings.'
            )

        verified_repos, unverified_repos = await JiraFactory._verify_repos(
            payload.issue_key, potential_repos, provider_handler
        )
        if not verified_repos:
            raise RepositorySelectionError(
                f'Could not access any of the mentioned repositories: '
                f'{", ".join(potential_repos)}. No conversation was started. '
                'Please ensure each repository exists and that the Git account '
                'linked to your OpenHands user can access it.'
            )

        primary, *additional = verified_repos
        view.selected_repo = primary.full_name
        view.git_provider = primary.git_provider
        view.additional_repos = additional[:MAX_ADDITIONAL_REPOSITORIES]
        view.skipped_repos = [
            r.full_name for r in additional[MAX_ADDITIONAL_REPOSITORIES:]
        ]
        view.inaccessible_repos = [m for m in unverified_repos if m in comment_mentions]
        logger.info(
            '[Jira] Verified repository access',
            extra={
                'issue_key': payload.issue_key,
                'repository': primary.full_name,
                'additional_repositories': [r.full_name for r in view.additional_repos],
                'inaccessible_repositories': view.inaccessible_repos,
                'skipped_repositories': view.skipped_repos,
            },
        )

    @staticmethod
    async def create_view(
        payload: JiraWebhookPayload,
        workspace: JiraWorkspace,
        user: JiraUser,
        user_auth: UserAuth,
        decrypted_api_key: str,
    ) -> JiraViewInterface:
        """Create a Jira view with the mentioned repositories, if any.

        This factory method:
        1. Creates the view with payload and auth context
        2. Fetches issue details (needed for repo inference)
        3. Infers and selects the repositories that are mentioned

        If any step fails, an appropriate exception is raised with
        a user-friendly message.

        Args:
            payload: Parsed webhook payload
            workspace: The Jira workspace
            user: The Jira user
            user_auth: OpenHands user authentication
            decrypted_api_key: Decrypted service account API key

        Returns:
            A JiraViewInterface with selected_repo (the first verified mention)
            and additional_repos set when mentions were verified

        Raises:
            StartingConvoException: If view creation fails
            RepositorySelectionError: If repositories are mentioned but none
                can be used
        """
        logger.info(
            '[Jira] Creating view',
            extra={
                'issue_key': payload.issue_key,
                'event_type': payload.event_type.value,
            },
        )

        view = JiraNewConversationView(
            payload=payload,
            saas_user_auth=user_auth,
            jira_user=user,
            jira_workspace=workspace,
            _decrypted_api_key=decrypted_api_key,
        )

        try:
            issue_title, issue_description = await view.get_issue_details()
        except StartingConvoException:
            raise
        except Exception as e:
            raise StartingConvoException('Failed to fetch issue details') from e

        await JiraFactory._select_repositories(view, issue_title, issue_description)

        logger.info(
            '[Jira] View created successfully',
            extra={
                'issue_key': payload.issue_key,
                'selected_repo': view.selected_repo,
                'git_provider': view.git_provider.value if view.git_provider else None,
                'additional_repos': [r.full_name for r in view.additional_repos],
            },
        )

        return view
