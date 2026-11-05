from __future__ import annotations

import logging
import shlex
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openhands.sdk.workspace.remote.async_remote_workspace import (
        AsyncRemoteWorkspace,
    )

COMMON_BRANCH_EXAMPLES = "'main', 'feature/foo', or 'release/1.2.3'"

# The identity the settings page shows when the user has saved none; the
# agent's system prompt names the same default.
DEFAULT_GIT_USER_NAME = 'openhands'
DEFAULT_GIT_USER_EMAIL = 'openhands@all-hands.dev'

_logger = logging.getLogger(__name__)


async def configure_git_user_settings(
    workspace: AsyncRemoteWorkspace,
    git_user_name: str | None,
    git_user_email: str | None,
) -> None:
    """Configure the saved Git identity in a workspace, or the default one.

    Without an identity the agent's commits fail with "Please tell me who you
    are".
    """
    for key, value in (
        ('user.name', git_user_name or DEFAULT_GIT_USER_NAME),
        ('user.email', git_user_email or DEFAULT_GIT_USER_EMAIL),
    ):
        command = f'git config --global {key} {shlex.quote(value)}'
        try:
            result = await workspace.execute_command(command, workspace.working_dir)
        except Exception:
            _logger.warning('Git config %s failed', key, exc_info=True)
            continue

        if result.exit_code:
            _logger.warning('Git config %s failed: %s', key, result.stderr)
        else:
            _logger.info('Git configured with %s=%s', key, value)


def ensure_valid_repository_name(repository: str) -> None:
    """Raise ValueError unless repository is a safe ``owner/repo`` style path.

    More segments are allowed for Azure DevOps (org/project/repo) and GitLab
    subgroups.
    """
    segments = repository.split('/')
    if len(segments) < 2 or not all(segments):
        raise ValueError(
            f"Invalid repository format: '{repository}'. Expected 'owner/repo' "
            "or more path segments (e.g. 'org/project/repo')."
        )
    if any(segment in ('.', '..') for segment in segments) or any(
        c in repository for c in [';', '&', '|', '$', '`', '\n', '\r']
    ):
        raise ValueError(f"Invalid characters in repository name: '{repository}'")


def is_valid_git_branch_name(branch_name: str) -> bool:
    """Return True when branch_name matches git branch naming rules."""
    if not branch_name:
        return False

    return (
        subprocess.run(
            ['git', 'check-ref-format', '--branch', branch_name],
            check=False,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


def ensure_valid_git_branch_name(branch_name: str) -> None:
    """Raise ValueError when branch_name is not safe to pass to git checkout."""
    if is_valid_git_branch_name(branch_name):
        return

    raise ValueError(
        f'Invalid git branch name. Common GitHub/GitLab/Bitbucket '
        f'branch names look like {COMMON_BRANCH_EXAMPLES}.'
    )
