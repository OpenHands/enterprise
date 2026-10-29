"""Repositories mentioned in an issue, PR or ticket, verified for one user.

Text-driven start paths (the git-provider resolvers and Jira DC) infer
``owner/repo`` mentions from the triggering comment and the issue/PR body,
verify each one with the user's linked providers and clone the verified ones
beside the primary repository.
"""

from dataclasses import dataclass, field

from integrations.utils import infer_repo_from_message
from openhands.app_server.app_conversation.additional_repositories import (
    MAX_ADDITIONAL_REPOSITORIES,
    AdditionalRepositoryRequest,
)
from openhands.app_server.integrations.provider import ProviderHandler
from openhands.app_server.integrations.service_types import ProviderType, Repository
from openhands.app_server.user_auth.user_auth import UserAuth
from openhands.app_server.utils.logger import openhands_logger as logger


@dataclass
class MentionedRepositories:
    """Repositories to clone beside the primary, and what to tell the user."""

    # Verified mentions other than the primary, in mention order.
    additional: list[Repository] = field(default_factory=list)
    # Mentions from the triggering comment that could not be accessed.
    inaccessible: list[str] = field(default_factory=list)
    # Verified mentions beyond the per-conversation limit.
    skipped: list[str] = field(default_factory=list)

    def additional_repository_requests(
        self,
    ) -> list[AdditionalRepositoryRequest] | None:
        # A failed clone is reported on the conversation and does not stop the
        # start (require_all stays off); neither does a name the request rejects.
        requests = []
        for repo in self.additional:
            try:
                requests.append(
                    AdditionalRepositoryRequest(
                        repository=repo.full_name, git_provider=repo.git_provider
                    )
                )
            except ValueError:
                logger.warning(f'Skipping invalid repository name {repo.full_name!r}')
        return requests or None

    def on_it_message(self, primary: str) -> str:
        """'I'm on it!', naming the repositories when there are several."""
        if not self.additional:
            return "I'm on it!"
        names = join_names([r.full_name for r in self.additional])
        return f"I'm on it in {primary}, together with {names}!"

    def notes(self) -> str:
        """Paragraphs about mentions that were not included, or ''."""
        notes = ''
        if self.inaccessible:
            notes += (
                f'\n\nCould not access: {", ".join(self.inaccessible)}. '
                'Please ensure each repository exists and that the Git account '
                'linked to your OpenHands user can access it.'
            )
        if self.skipped:
            notes += (
                f'\n\nNot included (a conversation can have at most '
                f'{MAX_ADDITIONAL_REPOSITORIES + 1} repositories): '
                f'{", ".join(self.skipped)}.'
            )
        return notes


def join_names(names: list[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    if len(names) < 2:
        return ''.join(names)
    return f'{", ".join(names[:-1])} and {names[-1]}'


async def create_provider_handler(user_auth: UserAuth) -> ProviderHandler | None:
    """Build a ProviderHandler for the user, or None if no provider is linked."""
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


async def verify_mentioned_repos(
    provider_handler: ProviderHandler,
    mentioned_repos: list[str],
    log_prefix: str,
    preferred_provider: ProviderType | None = None,
) -> tuple[list[Repository], list[str]]:
    """Resolve each mentioned repo with a targeted lookup.

    Returns the verified repos in mention order and the mentions that could
    not be resolved. Avoids enumerating the user's full repo list, which is
    capped and misses repos on large Bitbucket DC instances (40k+ repos).
    """
    # Dedupe by canonical full_name: two forms of one repo (case/URL variants,
    # a rename) resolve to the same repo and must count once.
    verified: dict[str, Repository] = {}
    unverified: list[str] = []
    for repo_name in mentioned_repos:
        try:
            if preferred_provider:
                repo = await provider_handler.verify_repo_provider(
                    repo_name, preferred_provider, is_optional=True
                )
            else:
                repo = await provider_handler.verify_repo_provider(
                    repo_name, is_optional=True
                )
        except Exception as e:
            # INFO (not DEBUG) so the reason is visible in support bundles;
            # e carries the provider error incl. the attempted URL + status.
            logger.info(f'{log_prefix} Repo verification failed for {repo_name}: {e}')
            unverified.append(repo_name)
            continue
        verified.setdefault(repo.full_name.lower(), repo)
    return list(verified.values()), unverified


def select_additional_repositories(
    primary: str,
    verified: list[Repository],
    unverified: list[str],
    comment_mentions: list[str],
) -> MentionedRepositories:
    """Split verified mentions into the repositories cloned beside ``primary``.

    Only mentions from the comment that could not be accessed are reported:
    unverified text from the description is usually not a repository at all.
    """
    others = [r for r in verified if r.full_name.lower() != primary.lower()]
    return MentionedRepositories(
        additional=others[:MAX_ADDITIONAL_REPOSITORIES],
        inaccessible=[m for m in unverified if m in comment_mentions],
        skipped=[r.full_name for r in others[MAX_ADDITIONAL_REPOSITORIES:]],
    )


async def resolve_mentioned_repositories(
    user_auth: UserAuth,
    primary: str,
    provider: ProviderType,
    comment: str,
    body: str,
    log_prefix: str,
) -> MentionedRepositories:
    """Repositories a resolver clones beside the webhook's repository.

    Mentions in the triggering comment come first: they are the user's
    deliberate choice, while the body often links to issues or docs that also
    look like a/b. The webhook's repository stays the primary. Never raises:
    a failure here starts the conversation with the primary only, as before.
    """
    comment_mentions = infer_repo_from_message(comment or '')
    mentions = [
        m
        for m in dict.fromkeys(
            [*comment_mentions, *infer_repo_from_message(body or '')]
        )
        if m.lower() != primary.lower()
    ]
    if not mentions:
        return MentionedRepositories()

    try:
        provider_handler = await create_provider_handler(user_auth)
        if provider_handler:
            verified, unverified = await verify_mentioned_repos(
                provider_handler, mentions, log_prefix, preferred_provider=provider
            )
        else:
            verified, unverified = [], list(mentions)
    except Exception:
        logger.exception(f'{log_prefix} Could not verify mentioned repositories')
        return MentionedRepositories()

    selection = select_additional_repositories(
        primary, verified, unverified, comment_mentions
    )
    logger.info(
        '%s repo resolution primary=%s inferred=%s additional=%s',
        log_prefix,
        primary,
        mentions,
        [r.full_name for r in selection.additional],
    )
    return selection
