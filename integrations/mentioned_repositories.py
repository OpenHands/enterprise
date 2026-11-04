"""Repositories mentioned in a ticket or message, verified for one user.

Text-driven start paths (Jira DC today) infer ``owner/repo`` mentions from the
user's text, verify each one with the user's linked providers and start the
conversation with the verified ones.
"""

from dataclasses import dataclass, field

from openhands.app_server.app_conversation.additional_repositories import (
    MAX_ADDITIONAL_REPOSITORIES,
)
from openhands.app_server.integrations.provider import ProviderHandler
from openhands.app_server.integrations.service_types import Repository
from openhands.app_server.user_auth.user_auth import UserAuth
from openhands.app_server.utils.logger import openhands_logger as logger


@dataclass
class MentionedRepositories:
    """Repositories to clone beside the primary, and what to tell the user."""

    # Verified mentions other than the primary, in mention order.
    additional: list[Repository] = field(default_factory=list)
    # Mentions that could not be accessed and are worth reporting.
    inaccessible: list[str] = field(default_factory=list)
    # Verified mentions beyond the per-conversation limit.
    skipped: list[str] = field(default_factory=list)

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
    reportable: list[str],
) -> MentionedRepositories:
    """Split verified mentions into the repositories cloned beside ``primary``.

    Only unverified mentions listed in ``reportable`` are reported: other text
    that looks like a/b is usually not a repository at all.
    """
    others = [r for r in verified if r.full_name.lower() != primary.lower()]
    return MentionedRepositories(
        additional=others[:MAX_ADDITIONAL_REPOSITORIES],
        inaccessible=[m for m in unverified if m in reportable],
        skipped=[r.full_name for r in others[MAX_ADDITIONAL_REPOSITORIES:]],
    )
