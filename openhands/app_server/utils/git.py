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

_logger = logging.getLogger(__name__)


async def configure_git_user_settings(
    workspace: AsyncRemoteWorkspace,
    git_user_name: str | None,
    git_user_email: str | None,
) -> None:
    """Configure the saved Git identity in a workspace."""
    for key, value in (
        ('user.name', git_user_name),
        ('user.email', git_user_email),
    ):
        if not value:
            continue

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
