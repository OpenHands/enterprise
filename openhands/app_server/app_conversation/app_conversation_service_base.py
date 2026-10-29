import asyncio
import base64
import binascii
import logging
import os
import re
import shlex
import tempfile
from abc import ABC
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncGenerator
from uuid import UUID

from openhands.sdk.settings import ACPAgentSettings, OpenHandsAgentSettings

if TYPE_CHECKING:
    import httpx

import base62

from openhands.app_server.app_conversation.additional_repositories import (
    AdditionalRepository,
    AdditionalRepositoryRequest,
    AdditionalRepositoryStatus,
    assign_repository_folders,
    merge_repository_skills,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    AgentType,
    AppConversationStartTask,
    AppConversationStartTaskStatus,
)
from openhands.app_server.app_conversation.app_conversation_service import (
    AppConversationService,
)
from openhands.app_server.app_conversation.skill_loader import (
    authenticate_marketplace_sources,
    azure_devops_marketplace_orgs,
    azure_devops_user_orgs,
    build_org_configs,
    build_sandbox_config,
    get_azure_devops_bearer_token,
    is_agent_server_repository_source,
    load_skills_from_agent_server,
)
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.sandbox.sandbox_models import SandboxInfo
from openhands.app_server.settings.settings_models import MarketplaceRegistration
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.utils.git import (
    configure_git_user_settings,
    ensure_valid_git_branch_name,
)
from openhands.sdk import Agent, LLMSummarizingCondenser
from openhands.sdk.context import AgentContext
from openhands.sdk.llm import LLM
from openhands.sdk.secret import SecretSource
from openhands.sdk.security import (
    AlwaysConfirm,
    ConfirmationPolicyBase,
    ConfirmRisky,
    LLMSecurityAnalyzer,
    NeverConfirm,
    SecurityAnalyzerBase,
)
from openhands.sdk.skills import Skill
from openhands.sdk.utils.redact import redact_url_credentials_in_text
from openhands.sdk.workspace.remote.async_remote_workspace import AsyncRemoteWorkspace

_logger = logging.getLogger(__name__)


def _warn_unresolved_marketplace_sources(
    registered_marketplaces: list[MarketplaceRegistration] | None,
) -> None:
    """Log auto-load marketplaces the agent-server will not treat as repositories."""
    for reg in registered_marketplaces or []:
        source = getattr(reg, 'source', None)
        if (
            getattr(reg, 'auto_load', False) is True
            and isinstance(source, str)
            and not is_agent_server_repository_source(source)
        ):
            _logger.warning(
                'Marketplace %r source %r is not a repository address the '
                'agent-server can clone; it will not load from a repository',
                reg.name,
                source,
            )


PRE_COMMIT_HOOK = '.git/hooks/pre-commit'
PRE_COMMIT_LOCAL = '.git/hooks/pre-commit.local'

# Secret names that carry a user-configured GPG signing key (case-insensitive).
# The secret value is the ASCII-armored private key (optionally base64-encoded)
# and must be passphrase-less so signing works headlessly in the sandbox.
_GPG_SECRET_NAMES = frozenset({'GPG_KEY', 'GPG_PRIVATE_KEY'})

_ADDITIONAL_CLONE_CONCURRENCY = 3


def azure_devops_repositories(
    additional_repositories: Sequence[
        AdditionalRepositoryRequest | AdditionalRepository
    ]
    | None,
) -> list[str]:
    """Names of the Azure DevOps repositories among ``additional_repositories``."""
    return [
        repo.repository
        for repo in additional_repositories or []
        if repo.git_provider == ProviderType.AZURE_DEVOPS
    ]


def get_project_dir(
    working_dir: str,
    selected_repository: str | None = None,
) -> str:
    """Get the project root directory for a conversation.

    When a repository is selected, the project root is the cloned repo directory
    at {working_dir}/{repo_name}.  This is the directory that contains the
    `.openhands/` configuration (setup.sh, pre-commit.sh, skills/, etc.).

    Without a repository, the project root is the working_dir itself.

    This must be used consistently for ALL features that depend on the project root:
    - workspace.working_dir (terminal CWD, file editor root, etc.)
    - .openhands/setup.sh execution
    - .openhands/pre-commit.sh (git hooks setup)
    - .openhands/skills/ (project skills)
    - PLAN.md path

    Args:
        working_dir: Base working directory path in the sandbox
            (e.g., '/workspace/project' from sandbox_spec)
        selected_repository: Repository name (e.g., 'OpenHands/software-agent-sdk')
            If provided, the repo name is appended to working_dir.

    Returns:
        The project root directory path.
    """
    if selected_repository:
        repo_name = selected_repository.split('/')[-1]
        return f'{working_dir}/{repo_name}'
    return working_dir


# Per-conversation file holding the Azure DevOps exchange JWT the credential
# helper reads. Written fresh for each conversation (like GitHub/GitLab re-embed
# their token per conversation), mode 600. The ADO token the helper returns to
# git is never persisted — only this short-lived exchange JWT is, and only in the
# sandbox the conversation controls.
_AZURE_DEVOPS_ACCESS_TOKEN_FILE = '$HOME/.openhands/azure-devops-access-token'


def _azure_devops_credential_helper_script(web_url: str | None) -> str:
    """Git credential helper fetching the user's Azure DevOps token on demand.

    Reads the per-conversation, time-limited access-token JWT from
    ``_AZURE_DEVOPS_ACCESS_TOKEN_FILE`` and exchanges it at
    ``/api/v1/webhooks/secrets`` for the user's Azure DevOps token. The JWT is
    delivered per conversation (not a sandbox-lifetime env var), so a conversation
    that reuses a running sandbox gets its own fresh, masked credential. The ADO
    token itself is never written to disk; only this script, its config and the
    short-lived exchange JWT are.
    """
    app_base_url = shlex.quote(web_url if isinstance(web_url, str) else '')
    token_file = _AZURE_DEVOPS_ACCESS_TOKEN_FILE
    return f"""#!/bin/sh
if [ "$1" != "get" ]; then
  exit 0
fi

token_file="{token_file}"
access_token=""
if [ -r "$token_file" ]; then
  access_token="$(cat "$token_file" 2>/dev/null)"
fi
webhook_url="${{OH_WEBHOOKS_0_BASE_URL:-}}"
app_base_url={app_base_url}
if [ -n "$webhook_url" ]; then
  base_url="${{webhook_url%/api/v1/webhooks}}"
else
  base_url="$app_base_url"
fi
if [ -z "$access_token" ] || [ -z "$base_url" ]; then
  exit 0
fi

secret_url="$base_url/api/v1/webhooks/secrets"
token="$(curl -fsS \\
  -H "X-Access-Token: $access_token" \\
  "$secret_url" 2>/dev/null)"
if [ -z "$token" ]; then
  echo "openhands: Azure DevOps credential exchange failed (token may be expired)" >&2
  exit 0
fi

printf '%s\\n' "username=oauth2"
printf 'password=%s\\n' "$token"
"""


# Per-conversation exchange JWTs for the other git providers, one file per
# provider (``<provider>.token``, mode 600), read by the shared credential helper
# below. The provider token the helper returns to git is never persisted.
_GIT_CREDENTIAL_TOKEN_DIR = '$HOME/.openhands/git-credentials'
_GIT_CREDENTIAL_HELPER = '$HOME/.openhands/git-credential-helper'

# The git username each provider pairs with a token. Bitbucket tokens stored as
# ``user:secret`` carry their own username, which the helper uses instead.
_GIT_CREDENTIAL_USERNAMES = {
    ProviderType.GITHUB: 'x-access-token',
    ProviderType.GITLAB: 'oauth2',
    ProviderType.BITBUCKET: 'x-token-auth',
    ProviderType.BITBUCKET_DATA_CENTER: 'x-token-auth',
    ProviderType.FORGEJO: 'x-access-token',
}


def _git_credential_helper_script(web_url: str | None) -> str:
    """Git credential helper fetching a provider token on demand.

    Installed once per sandbox and configured per provider host as
    ``credential.<protocol>://<host>.helper = !<helper> <provider>``. On ``get``
    it reads that provider's per-conversation exchange JWT from
    ``_GIT_CREDENTIAL_TOKEN_DIR`` and exchanges it at ``/api/v1/webhooks/secrets``
    for the user's current token. A ``user:secret`` token (Bitbucket) supplies
    the username; a bare token or the ``x-token-auth:<token>`` form leaves the
    configured ``credential.<host>.username`` in place.
    Mirrors ``_azure_devops_credential_helper_script``.
    """
    app_base_url = shlex.quote(web_url if isinstance(web_url, str) else '')
    return f"""#!/bin/sh
provider="$1"
if [ "$2" != "get" ]; then
  exit 0
fi
cat > /dev/null
token_file="{_GIT_CREDENTIAL_TOKEN_DIR}/$provider.token"
access_token=""
if [ -r "$token_file" ]; then
  access_token="$(cat "$token_file" 2>/dev/null)"
fi
webhook_url="${{OH_WEBHOOKS_0_BASE_URL:-}}"
app_base_url={app_base_url}
if [ -n "$webhook_url" ]; then
  base_url="${{webhook_url%/api/v1/webhooks}}"
else
  base_url="$app_base_url"
fi
if [ -z "$access_token" ] || [ -z "$base_url" ]; then
  exit 0
fi

secret_url="$base_url/api/v1/webhooks/secrets"
secret="$(curl -fsS \\
  -H "X-Access-Token: $access_token" \\
  "$secret_url" 2>/dev/null)"
if [ -z "$secret" ]; then
  echo "openhands: $provider credential exchange failed (token may be expired)" >&2
  exit 0
fi

case "$secret" in
  x-token-auth:*)
    # Not a user: keep the username configured for this host.
    printf 'password=%s\\n' "${{secret#*:}}"
    ;;
  *:*)
    printf 'username=%s\\n' "${{secret%%:*}}"
    printf 'password=%s\\n' "${{secret#*:}}"
    ;;
  *)
    printf 'password=%s\\n' "$secret"
    ;;
esac
"""


@dataclass
class AppConversationServiceBase(AppConversationService, ABC):
    """App Conversation service which adds git specific functionality.

    Sets up repositories and installs hooks
    """

    init_git_in_empty_workspace: bool
    user_context: UserContext

    async def load_and_merge_all_skills(
        self,
        sandbox: SandboxInfo,
        selected_repository: str | None,
        project_dir: str,
        agent_server_url: str,
        registered_marketplaces: list[MarketplaceRegistration] | None = None,
        additional_repositories: Sequence[AdditionalRepository] = (),
    ) -> list[Skill]:
        """Load skills from all sources via the agent-server.

        This method calls the agent-server's /api/skills endpoint to load and
        merge skills from all sources. The agent-server handles:
        - Public skills (from OpenHands/skills GitHub repo)
        - User skills (from ~/.openhands/skills/)
        - Organization skills (from {org}/.openhands repo)
        - Project/repo skills (from repo .agents/skills/, .openhands/microagents/, and legacy .openhands/skills/)
        - Sandbox skills (from exposed URLs)
        - Registered marketplaces (from user settings)

        Args:
            sandbox: SandboxInfo containing exposed URLs and agent-server URL
            selected_repository: Repository name or None
            project_dir: Project root directory (resolved via get_project_dir).
            agent_server_url: Agent-server URL (required)
            registered_marketplaces: Optional list of MarketplaceRegistration objects
                from user settings. Marketplaces with auto_load=True will have
                their plugins loaded automatically.
            additional_repositories: Repositories cloned beside the main one;
                each cloned one's project skills are loaded with its own call.

        Returns:
            List of merged Skill objects from all sources, or empty list on failure
        """
        try:
            _logger.debug('Loading skills for V1 conversation via agent-server')

            if not agent_server_url:
                _logger.warning('No agent-server URL available, cannot load skills')
                return []

            # Build org configs (authentication handled by app-server)
            org_configs = await build_org_configs(
                selected_repository,
                self.user_context,
                [repo.repository for repo in additional_repositories],
            )

            # Build sandbox config (exposed URLs)
            sandbox_config = build_sandbox_config(sandbox)

            await self._configure_marketplace_git_credentials(
                sandbox, agent_server_url, project_dir, registered_marketplaces
            )

            # Swap marketplace sources for authenticated git URLs so the
            # agent-server can clone private marketplace repos. Never raises;
            # unresolvable sources pass through unchanged.
            registered_marketplaces = await authenticate_marketplace_sources(
                registered_marketplaces, self.user_context
            )
            _warn_unresolved_marketplace_sources(registered_marketplaces)

            # Single API call to agent-server for ALL skills
            all_skills = await load_skills_from_agent_server(
                agent_server_url=agent_server_url,
                session_api_key=sandbox.session_api_key,
                project_dir=project_dir,
                org_configs=org_configs,
                sandbox_config=sandbox_config,
                load_public=True,
                load_user=True,
                # Without a primary the project folder is the parent of the
                # clones; their skills load per repository below instead.
                load_project=bool(selected_repository) or not additional_repositories,
                load_org=True,
                registered_marketplaces=registered_marketplaces,
            )

            cloned = [
                repo
                for repo in additional_repositories
                if repo.status == AdditionalRepositoryStatus.CLONED
            ]
            # The agent works from the parent folder whenever additional
            # repositories were requested, even if none of them cloned.
            if additional_repositories:
                per_repository = await asyncio.gather(
                    *(
                        load_skills_from_agent_server(
                            agent_server_url=agent_server_url,
                            session_api_key=sandbox.session_api_key,
                            project_dir=repo.path,
                            load_public=False,
                            load_user=False,
                            load_project=True,
                            load_org=False,
                        )
                        for repo in cloned
                    )
                )
                all_skills = merge_repository_skills(
                    all_skills,
                    project_dir if selected_repository else None,
                    selected_repository.split('/')[-1] if selected_repository else None,
                    list(zip(cloned, per_repository, strict=True)),
                )

            _logger.info(
                f'Loaded {len(all_skills)} total skills from agent-server: '
                f'{[s.name for s in all_skills]}'
            )

            return all_skills

        except Exception as e:
            _logger.warning(f'Failed to load skills: {e}', exc_info=True)
            # Return empty list on failure - skills will be loaded again later if needed
            return []

    def _create_agent_with_skills(self, agent, skills: list[Skill]):
        """Create or update agent with skills in its context.

        Args:
            agent: The agent to update
            skills: List of Skill objects to add to agent context

        Returns:
            Updated agent with skills in context
        """
        if agent.agent_context:
            # Merge with existing context (new skills override existing ones)
            existing_skills = agent.agent_context.skills
            all_skills = self._merge_skills([existing_skills, skills])
            agent = agent.model_copy(
                update={
                    'agent_context': agent.agent_context.model_copy(
                        update={'skills': all_skills}
                    )
                }
            )
        else:
            # Create new context
            agent_context = AgentContext(skills=skills)
            agent = agent.model_copy(update={'agent_context': agent_context})

        return agent

    def _merge_skills(self, skill_lists: list[list[Skill]]) -> list[Skill]:
        """Merge multiple skill lists, avoiding duplicates by name.

        Later lists take precedence over earlier lists for duplicate names.

        Args:
            skill_lists: List of skill lists to merge

        Returns:
            Deduplicated list of skills with later lists overriding earlier ones
        """
        skills_by_name: dict[str, Skill] = {}

        for skill_list in skill_lists:
            for skill in skill_list:
                skills_by_name[skill.name] = skill

        return list(skills_by_name.values())

    async def _load_skills_and_update_agent(
        self,
        sandbox: SandboxInfo,
        agent: Agent | OpenHandsAgentSettings | ACPAgentSettings,
        remote_workspace: AsyncRemoteWorkspace,
        selected_repository: str | None,
        project_dir: str,
        disabled_skills: list[str] | None = None,
        registered_marketplaces: list[MarketplaceRegistration] | None = None,
        additional_repositories: Sequence[AdditionalRepository] = (),
    ):
        """Load all skills and update agent with them.

        Args:
            agent: The agent to update
            remote_workspace: AsyncRemoteWorkspace for loading repo skills
            selected_repository: Repository name or None (used for org config)
            project_dir: Project root directory (already resolved via get_project_dir).
            disabled_skills: Optional list of skill names to exclude
            registered_marketplaces: Optional composed marketplaces (instance +
                org + user) whose auto_load plugins the agent-server should load.
            additional_repositories: Repositories cloned beside the main one.

        Returns:
            Updated agent with skills loaded into context
        """
        agent_server_url = remote_workspace.host
        all_skills = await self.load_and_merge_all_skills(
            sandbox,
            selected_repository,
            project_dir,
            agent_server_url,
            registered_marketplaces=registered_marketplaces,
            additional_repositories=additional_repositories,
        )

        # Filter out disabled skills
        if disabled_skills:
            disabled_set = set(disabled_skills)
            all_skills = [s for s in all_skills if s.name not in disabled_set]

        # Update agent with skills
        agent = self._create_agent_with_skills(agent, all_skills)

        return agent

    async def run_setup_scripts(
        self,
        task: AppConversationStartTask,
        sandbox: SandboxInfo,
        workspace: AsyncRemoteWorkspace,
        agent_server_url: str,
        conversation_id: UUID,
    ) -> AsyncGenerator[AppConversationStartTask, None]:
        task.status = AppConversationStartTaskStatus.PREPARING_REPOSITORY
        yield task
        await self.clone_or_init_git_repo(task, workspace, sandbox)

        # Deliver this conversation's Azure DevOps credential (per conversation,
        # like GitHub/GitLab re-embed their token), independent of which repo was
        # selected, so plain git works on any of the user's Azure DevOps repos.
        await self._configure_azure_devops_git_credentials(
            workspace,
            task._azure_devops_access_token,
            task.request.selected_repository,
            azure_devops_repositories(task.request.additional_repositories),
        )
        # Same for the other providers: a helper per provider host, so hooks and
        # the agent can clone any repository the user can reach with plain git.
        await self._configure_git_credential_helpers(
            workspace, getattr(task, '_git_credential_access_tokens', None)
        )

        task._additional_repositories = await self.clone_additional_repositories(
            task, workspace
        )
        failed = [
            repo.repository
            for repo in task._additional_repositories
            if repo.status == AdditionalRepositoryStatus.FAILED
        ]
        if failed:
            task.detail = 'Could not clone additional repositories: ' + ', '.join(
                failed
            )
            if task.request.require_all:
                raise ValueError(task.detail)

        # Compute the project root — the cloned repo directory when a repo is
        # selected, or the sandbox working_dir otherwise.  This must be used
        # for all .openhands/ features (setup.sh, pre-commit.sh, skills).
        project_dir = get_project_dir(
            workspace.working_dir, task.request.selected_repository
        )

        task.status = AppConversationStartTaskStatus.RUNNING_SETUP_SCRIPT
        yield task
        await self.maybe_run_setup_script(workspace, project_dir)

        task.status = AppConversationStartTaskStatus.SETTING_UP_GIT_HOOKS
        yield task
        await self.maybe_setup_git_hooks(workspace, project_dir)
        for repo in task._additional_repositories:
            if repo.status == AdditionalRepositoryStatus.CLONED:
                await self.maybe_setup_git_hooks(workspace, repo.path)

        # Inject the user's stored memory context into the sandbox before the
        # conversation starts. This writes the MEMORY.md file so the SDK's
        # load_memory() picks it up on the first send_message/run.
        await self.maybe_inject_memory_context(workspace, project_dir)

        task.status = AppConversationStartTaskStatus.SETTING_UP_SKILLS
        yield task
        await self.load_and_merge_all_skills(
            sandbox,
            task.request.selected_repository,
            project_dir,
            agent_server_url,
        )

    async def _configure_git_user_settings(
        self,
        workspace: AsyncRemoteWorkspace,
    ) -> None:
        """Configure git global user settings from user preferences.

        Reads git_user_name and git_user_email from user settings and
        configures them (or the default identity) as git global settings in
        the workspace.

        Args:
            workspace: The remote workspace to configure git settings in.
        """
        try:
            user_info = await self.user_context.get_user_info()
            await configure_git_user_settings(
                workspace,
                user_info.git_user_name,
                user_info.git_user_email,
            )
        except Exception as e:
            _logger.warning(f'Failed to configure git user settings: {e}')

    async def _configure_gpg_signing(
        self,
        workspace: AsyncRemoteWorkspace,
    ) -> None:
        """Configure GPG commit signing from a user-configured signing key.

        If the user has registered a secret named ``GPG_KEY`` or
        ``GPG_PRIVATE_KEY`` (case-insensitive), the armored private key is
        imported into the sandbox keyring, signing is enabled, and the commit
        author identity is pinned to the key's UID so that GitHub (and other
        hosts) mark commits as "Verified".

        Requirements on the key:
            - It must be the **private** key (``-----BEGIN PGP PRIVATE KEY
              BLOCK-----``), not just the public key -- only the private half
              can sign.
            - It must have **no passphrase** (empty protection). A passphrase
              would require a TTY/pinentry that does not exist in the headless
              sandbox. The standard automated-signing approach is a
              passphrase-less key.
            - The secret value may be raw ASCII-armored text **or**
              base64-encoded; both are accepted.

        How to create a suitable key (run on a trusted workstation)::

            # 1. Generate a passphrase-less signing key.
            cat > /tmp/keyparam <<'EOF'
            %no-protection
            Key-Type: RSA
            Key-Length: 4096
            Name-Real: Your Name
            Name-Email: you@example.com
            Expire-Date: 1y
            %commit
            EOF
            gpg --batch --generate-key /tmp/keyparam

            # 2. Export the PRIVATE key (NOT --export-secret-subkeys, which
            #    omits the primary key). The export must NOT prompt.
            KEY_ID=$(gpg --list-secret-keys --keyid-format=long \\
                | awk '/^sec/{print $2}' | cut -d/ -f2 | head -1)
            gpg --export-secret-keys --armor "$KEY_ID" > gpg-private.asc

            # 3. (Optional) base64-encode for single-line storage.
            base64 -w0 gpg-private.asc > gpg-private.b64

            # 4. Register the value as a secret named GPG_KEY (or
            #    GPG_PRIVATE_KEY) in the OpenHands Secrets panel.

            # 5. Register the PUBLIC key on GitHub so commits show
            #    "Verified": Settings -> SSH and GPG keys -> New GPG key,
            #    paste `gpg --armor --export "$KEY_ID"`.

        Args:
            workspace: The remote workspace to configure GPG signing in.
        """
        try:
            secrets = await self.user_context.get_secrets()
            gpg_entry = self._find_gpg_secret(secrets)
            if gpg_entry is None:
                return  # No signing key configured -- signing stays off.

            secret_name, secret_source = gpg_entry
            key_material = secret_source.get_value()
            if not key_material:
                _logger.warning(
                    f'GPG signing key secret {secret_name} is empty; '
                    'commit signing disabled.'
                )
                return

            # Accept base64-encoded or raw ASCII-armored key material.
            armored = self._decode_key_material(key_material)
            if armored is None:
                _logger.warning(
                    f'GPG signing key {secret_name} is not valid ASCII-armored '
                    f'or base64-encoded key material; commit signing disabled.'
                )
                return

            # Write the key to a temp file in the sandbox, import, then shred.
            # A file (rather than a heredoc on the command line) keeps the key
            # material out of the process list.
            key_path = f'/tmp/.oh-gpg-import-{base62.encodebytes(os.urandom(16))}'
            result = await workspace.execute_command(
                f'printf %s {shlex.quote(armored)} > {shlex.quote(key_path)}',
                workspace.working_dir,
            )
            if result.exit_code:
                _logger.warning(f'Failed to write GPG key file: {result.stderr}')
                return

            try:
                # Reload gpg-agent so a stale (possibly passphrase-protected)
                # cached key cannot shadow the fresh import.
                await workspace.execute_command(
                    'gpgconf --kill gpg-agent 2>/dev/null || true',
                    workspace.working_dir,
                )
                import_result = await workspace.execute_command(
                    f'gpg --batch --import {shlex.quote(key_path)}',
                    workspace.working_dir,
                )
            finally:
                await workspace.execute_command(
                    f'rm -f {shlex.quote(key_path)}',
                    workspace.working_dir,
                )

            if import_result.exit_code:
                _logger.warning(
                    f'GPG key import failed (exit {import_result.exit_code}): '
                    f'{import_result.stderr}'
                )
                return

            # Parse the imported key for the key ID and the UID's email.
            list_result = await workspace.execute_command(
                'gpg --list-secret-keys --keyid-format=long --with-colons',
                workspace.working_dir,
            )
            key_id, key_email, key_name = self._parse_gpg_key_info(list_result.stdout)
            if not key_id:
                _logger.warning(
                    'GPG key imported but no usable secret key found. '
                    'Ensure the secret contains the PRIVATE key (not just the '
                    'public key) and is passphrase-less.'
                )
                return

            # Enable signing and pin the key.
            await self._run_git_config(workspace, 'commit.gpgsign', 'true')
            await self._run_git_config(workspace, 'user.signingkey', key_id)

            # Pin the commit author to the key's UID. GitHub marks a commit
            # "Verified" only when the author email matches the key's UID, so
            # the key's identity overrides any pre-set git user settings.
            if key_email:
                await self._run_git_config(workspace, 'user.email', key_email)
            if key_name:
                await self._run_git_config(workspace, 'user.name', key_name)

            _logger.info(
                f'GPG commit signing enabled with key {key_id} '
                f'(uid={key_name} <{key_email}>)'
            )
        except Exception as e:
            _logger.warning(f'Failed to configure GPG signing: {e}')

    def _find_gpg_secret(
        self, secrets: dict[str, SecretSource]
    ) -> tuple[str, SecretSource] | None:
        """Return the (name, source) of the configured GPG secret, if any."""
        for name, source in secrets.items():
            if name and name.upper() in _GPG_SECRET_NAMES:
                return name, source
        return None

    @staticmethod
    def _decode_key_material(value: str) -> str | None:
        """Return ASCII-armored key text, accepting raw or base64 input.

        Returns ``None`` if the value is neither valid ASCII-armored PGP key
        material nor decodable base64 that yields such material.
        """
        if 'BEGIN PGP PRIVATE KEY BLOCK' in value:
            return value
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            return None
        decoded_str = decoded.decode('utf-8', errors='replace')
        if 'BEGIN PGP PRIVATE KEY BLOCK' in decoded_str:
            return decoded_str
        return None

    @staticmethod
    def _parse_gpg_key_info(
        colons_output: str,
    ) -> tuple[str | None, str | None, str | None]:
        """Extract (key_id, email, name) from ``gpg --with-colons`` output.

        Looks for the first ``sec:`` line (the primary secret key) for the key
        ID, and the first ``uid:`` line for the user ID string. Returns
        ``(None, None, None)`` if no secret key is listed.
        """
        key_id: str | None = None
        email: str | None = None
        name: str | None = None
        for line in colons_output.splitlines():
            fields = line.split(':')
            if fields[0] == 'sec' and not key_id:
                # Field 5 is the key ID in --keyid-format=long.
                if len(fields) > 4 and fields[4]:
                    key_id = fields[4]
            elif fields[0] == 'uid' and not email:
                # The user ID string (e.g. "Name <email>") lives in field 10
                # of the colon format, but some variants place it elsewhere;
                # scan for the field containing the angle-bracketed address.
                uid_str = ''
                for f in fields[1:]:
                    if '<' in f and '>' in f:
                        uid_str = f
                        break
                if not uid_str:
                    # Fall back to the last non-empty field.
                    for f in reversed(fields[1:]):
                        if f:
                            uid_str = f
                            break
                if uid_str:
                    match = re.search(r'<([^>]+)>', uid_str)
                    if match:
                        email = match.group(1)
                    name = uid_str.split(' <')[0].strip() or None
            if key_id and email:
                break
        return key_id, email, name

    async def _run_git_config(
        self,
        workspace: AsyncRemoteWorkspace,
        key: str,
        value: str,
    ) -> None:
        """Set a global git config value, logging on failure."""
        cmd = f'git config --global {shlex.quote(key)} {shlex.quote(value)}'
        result = await workspace.execute_command(cmd, workspace.working_dir)
        if result.exit_code:
            _logger.warning(f'Git config {key} failed: {result.stderr}')

    async def clone_or_init_git_repo(
        self,
        task: AppConversationStartTask,
        workspace: AsyncRemoteWorkspace,
        sandbox: SandboxInfo | None = None,
    ):
        request = task.request

        # Create the projects directory if it does not exist yet
        parent = Path(workspace.working_dir).parent
        result = await workspace.execute_command(
            f'mkdir -p {workspace.working_dir}', parent
        )
        if result.exit_code:
            _logger.warning(f'mkdir failed: {result.stderr}')

        # Configure git user settings from user preferences
        await self._configure_git_user_settings(workspace)

        # Configure GPG commit signing if the user registered a GPG key secret.
        # Runs after _configure_git_user_settings so the key's UID can override
        # the commit author identity for host-side verification.
        await self._configure_gpg_signing(workspace)

        if not request.selected_repository:
            # A parent folder holding additional repositories stays a plain folder.
            if self.init_git_in_empty_workspace and not request.additional_repositories:
                _logger.debug('Initializing a new git repository in the workspace.')
                cmd = (
                    'git init && git config --global '
                    f'--add safe.directory {workspace.working_dir}'
                )
                result = await workspace.execute_command(cmd, workspace.working_dir)
                if result.exit_code:
                    _logger.warning(f'Git init failed: {result.stderr}')
            else:
                _logger.info('Not initializing a new git repository.')
            return

        user_info = await self.user_context.get_user_info()
        remote_repo_url: str = await self.user_context.get_authenticated_git_url(
            request.selected_repository,
            specified_provider=request.git_provider,
        )
        if not remote_repo_url:
            raise ValueError('Missing either Git token or valid repository')

        dir_name = request.selected_repository.split('/')[-1]
        if request.selected_branch:
            ensure_valid_git_branch_name(request.selected_branch)

        full_clone = bool(getattr(user_info, 'git_full_clone', False))
        cloned = await self._clone_repository(
            workspace,
            remote_repo_url,
            dir_name,
            request.selected_branch,
            request.git_provider,
            full_clone,
        )
        if not cloned:
            at_branch = (
                f' at branch {request.selected_branch!r}'
                if request.selected_branch
                else ''
            )
            raise ValueError(
                'Could not clone the selected repository '
                f'{request.selected_repository!r}{at_branch}. Check that the '
                'repository and branch exist and that you have access to '
                'them, then try again.'
            )

    async def _clone_repository(
        self,
        workspace: AsyncRemoteWorkspace,
        remote_repo_url: str,
        dir_name: str,
        branch: str | None,
        git_provider: ProviderType | None,
        full_clone: bool,
    ) -> bool:
        """Clone a repository into ``dir_name`` and check out its branch.

        Returns False when nothing usable was cloned. Never includes git's
        output in errors: the URL and that output can hold credentials.
        """
        quoted_remote_repo_url = shlex.quote(remote_repo_url)
        quoted_dir_name = shlex.quote(dir_name)
        git_dir = Path(workspace.working_dir) / dir_name
        azure_devops_bearer_token = await self._get_azure_devops_bearer_token_for_git(
            git_provider,
            remote_repo_url,
        )

        clone_flags = ''
        if not full_clone:
            clone_flags = ' --depth 1'
            if branch:
                clone_flags += f' --branch {shlex.quote(branch)}'

        # Clone the repo - this is the slow part!
        if azure_devops_bearer_token:
            auth_header = shlex.quote(
                f'Authorization: Bearer {azure_devops_bearer_token}'
            )
            clone_command = (
                f'git -c http.extraheader={auth_header} clone{clone_flags} '
                f'{quoted_remote_repo_url} {quoted_dir_name}'
            )
        else:
            clone_command = (
                f'git clone{clone_flags} {quoted_remote_repo_url} {quoted_dir_name}'
            )
        result = await workspace.execute_command(
            clone_command, workspace.working_dir, 120
        )
        if result.exit_code:
            _logger.warning(
                f'Git clone failed: {redact_url_credentials_in_text(result.stderr)}'
            )
            # A conversation sharing its sandbox (e.g. a sub-conversation) finds
            # the repository already cloned and keeps using it. Otherwise,
            # including an empty repository left by an earlier failed clone,
            # nothing usable was cloned.
            existing = await workspace.execute_command(
                f'test -d {quoted_dir_name}/.git && '
                f'git -C {quoted_dir_name} rev-parse --verify --quiet HEAD',
                workspace.working_dir,
            )
            if existing.exit_code:
                return False

        # Checkout the appropriate branch
        if branch:
            # Needed for full clones; harmless no-op after shallow clones with --branch.
            checkout_command = f'git checkout {shlex.quote(branch)}'
        else:
            # Generate a random branch name to avoid conflicts
            random_str = base62.encodebytes(os.urandom(16))
            openhands_workspace_branch = f'openhands-workspace-{random_str}'
            checkout_command = (
                f'git checkout -b {shlex.quote(openhands_workspace_branch)}'
            )
        result = await workspace.execute_command(checkout_command, git_dir)
        if result.exit_code:
            _logger.warning(f'Git checkout failed: {result.stderr}')
        return True

    async def clone_additional_repositories(
        self,
        task: AppConversationStartTask,
        workspace: AsyncRemoteWorkspace,
    ) -> list[AdditionalRepository]:
        """Clone the request's additional repositories beside the main one.

        A repository that fails is reported in its result and never fails the
        start.
        """
        request = task.request
        if not request.additional_repositories:
            return []
        folders = assign_repository_folders(
            request.selected_repository, request.additional_repositories
        )
        user_info = await self.user_context.get_user_info()
        full_clone = bool(getattr(user_info, 'git_full_clone', False))
        semaphore = asyncio.Semaphore(_ADDITIONAL_CLONE_CONCURRENCY)

        async def clone(
            item: AdditionalRepositoryRequest, folder: str
        ) -> AdditionalRepository:
            async with semaphore:
                error = await self._clone_additional_repository(
                    workspace, item, folder, full_clone
                )
            return AdditionalRepository(
                repository=item.repository,
                branch=item.branch,
                git_provider=item.git_provider,
                folder=folder,
                path=f'{workspace.working_dir}/{folder}',
                status=(
                    AdditionalRepositoryStatus.FAILED
                    if error
                    else AdditionalRepositoryStatus.CLONED
                ),
                error=error,
            )

        return list(
            await asyncio.gather(
                *(
                    clone(item, folder)
                    for item, folder in zip(
                        request.additional_repositories, folders, strict=True
                    )
                )
            )
        )

    async def _clone_additional_repository(
        self,
        workspace: AsyncRemoteWorkspace,
        item: AdditionalRepositoryRequest,
        folder: str,
        full_clone: bool,
    ) -> str | None:
        """Clone one additional repository; return an error message on failure."""
        at_branch = f' at branch {item.branch!r}' if item.branch else ''
        failure = (
            f'Could not clone {item.repository!r}{at_branch}. Check that the '
            'repository and branch exist and that you have access to them.'
        )
        try:
            remote_repo_url = await self.user_context.get_authenticated_git_url(
                item.repository,
                specified_provider=item.git_provider,
            )
            if not remote_repo_url:
                return failure
            if await self._clone_repository(
                workspace,
                remote_repo_url,
                folder,
                item.branch,
                item.git_provider,
                full_clone,
            ):
                return None
        except Exception as exc:
            _logger.warning(
                'Additional repository clone failed: %s', type(exc).__name__
            )
        return failure

    async def _get_azure_devops_bearer_token_for_git(
        self,
        git_provider: ProviderType | None,
        remote_repo_url: str,
    ) -> str | None:
        if (
            git_provider != ProviderType.AZURE_DEVOPS
            and 'dev.azure.com' not in remote_repo_url
        ):
            return None

        return await get_azure_devops_bearer_token(self.user_context)

    async def _configure_azure_devops_git_credentials(
        self,
        workspace: AsyncRemoteWorkspace,
        access_token: str | None,
        selected_repository: str | None,
        additional_repositories: Sequence[str] = (),
    ) -> None:
        """Deliver this conversation's Azure DevOps credential to the sandbox.

        Writes the per-conversation exchange JWT to a mode-600 file, installs the
        credential helper globally under ``$HOME`` and wires it for every Azure
        DevOps organization the user can reach, so plain git works on any of the
        user's Azure DevOps repos, not only the selected one. Mirrors
        ``_configure_marketplace_git_credentials``. Skips entirely when no token
        was minted for this conversation, so a sandbox without one is untouched.
        Entra tokens cannot ride in the authenticated URL, so the helper supplies
        them instead.
        """
        if not access_token:
            return
        orgs = await azure_devops_user_orgs(
            self.user_context, selected_repository, additional_repositories
        )
        if not orgs:
            return

        web_url = getattr(self, 'web_url', None)
        if web_url is None:
            _logger.debug(
                'Azure DevOps git credential helper has no configured web_url; '
                'it will rely on OH_WEBHOOKS_0_BASE_URL at runtime.'
            )
        helper_script = _azure_devops_credential_helper_script(web_url)
        commands = [
            'mkdir -p "$HOME/.openhands"',
            'helper="$HOME/.openhands/azure-devops-credential-helper"',
            # Double-quoted so $HOME expands, matching the helper script's reader.
            f'token_file="{_AZURE_DEVOPS_ACCESS_TOKEN_FILE}"',
            f'printf %s {shlex.quote(helper_script)} > "$helper"',
            'chmod 700 "$helper"',
            # The token value is passed quoted and never echoed to stdout.
            f'printf %s {shlex.quote(access_token)} > "$token_file"',
            'chmod 600 "$token_file"',
            'git config --global credential.https://dev.azure.com.useHttpPath true',
        ]
        for org in orgs:
            prefix = f'credential.https://dev.azure.com/{org}'
            commands += [
                f'git config --global {shlex.quote(prefix + ".username")} oauth2',
                'git config --global --replace-all '
                f'{shlex.quote(prefix + ".helper")} "!\\"$helper\\""',
            ]
        # Run in '/', which always exists (the grouped per-conversation working
        # dir may not yet); every path here is absolute under $HOME.
        result = await workspace.execute_command(' && '.join(commands), '/')
        if result.exit_code:
            _logger.warning(
                f'Azure DevOps git credential setup failed: {result.stderr}'
            )

    async def _git_credential_username(self, provider_handler, provider) -> str:
        """The git username to pair with ``provider``'s token.

        Bitbucket Data Center decides per server and token which username it
        accepts beside a bare token; ask its service, as the clone URL does.
        """
        if provider == ProviderType.BITBUCKET_DATA_CENTER:
            try:
                service = provider_handler.get_service(provider)
                username, _ = await service.get_git_credentials()
                if username:
                    return username
            except Exception as exc:
                _logger.debug(
                    f'Bitbucket Data Center git username lookup failed: {exc}'
                )
        return _GIT_CREDENTIAL_USERNAMES.get(provider, 'x-access-token')

    async def _configure_git_credential_helpers(
        self,
        workspace: AsyncRemoteWorkspace,
        access_tokens: Mapping[str, str] | None,
    ) -> None:
        """Deliver this conversation's per-provider credentials to the sandbox.

        For each provider with a minted exchange JWT, writes the JWT to a
        mode-600 file and configures the shared credential helper for that
        provider's git host (``credential.<protocol>://<host>``) in the global
        git config, so plain ``git clone/fetch/push`` of any repository on that
        host, from the agent's terminal or a hook, authenticates with the
        user's current token. The selected repository keeps its authenticated
        remote URL, which git prefers over a helper. Skips entirely when nothing
        was minted. Azure DevOps is handled by
        ``_configure_azure_devops_git_credentials``.
        """
        if not isinstance(access_tokens, Mapping) or not access_tokens:
            return
        try:
            provider_handler = await self.user_context.get_provider_handler()
        except Exception as exc:
            _logger.debug(f'No provider handler for git credential helpers: {exc}')
            return

        web_url = getattr(self, 'web_url', None)
        if web_url is None:
            _logger.debug(
                'Git credential helper has no configured web_url; '
                'it will rely on OH_WEBHOOKS_0_BASE_URL at runtime.'
            )
        helper_script = _git_credential_helper_script(web_url)
        commands = [
            f'token_dir="{_GIT_CREDENTIAL_TOKEN_DIR}"',
            'mkdir -p "$token_dir"',
            'chmod 700 "$token_dir"',
            f'helper="{_GIT_CREDENTIAL_HELPER}"',
            f'printf %s {shlex.quote(helper_script)} > "$helper"',
            'chmod 700 "$helper"',
        ]
        configured: list[str] = []
        for value, token in access_tokens.items():
            try:
                provider = ProviderType(value)
            except ValueError:
                continue
            if provider == ProviderType.AZURE_DEVOPS or not token:
                continue
            try:
                protocol, host = provider_handler.get_git_host(provider)
            except ValueError as exc:
                _logger.warning(f'Skipping git credential helper for {value}: {exc}')
                continue
            if not host:
                continue
            username = await self._git_credential_username(provider_handler, provider)
            prefix = f'credential.{protocol}://{host}'
            commands += [
                # The token value is passed quoted and never echoed to stdout.
                f'printf %s {shlex.quote(token)} > "$token_dir/{value}.token"',
                f'chmod 600 "$token_dir/{value}.token"',
                f'git config --global {shlex.quote(prefix + ".username")} '
                f'{shlex.quote(username)}',
                'git config --global --replace-all '
                f'{shlex.quote(prefix + ".helper")} "!\\"$helper\\" {value}"',
            ]
            configured.append(f'{value}@{host}')
        if not configured:
            return
        # Run in '/', which always exists; every path here is absolute under $HOME.
        result = await workspace.execute_command(' && '.join(commands), '/')
        if result.exit_code:
            _logger.warning(f'Git credential helper setup failed: {result.stderr}')
        else:
            _logger.info(f'Git credential helpers configured for {configured}')

    async def _configure_marketplace_git_credentials(
        self,
        sandbox: SandboxInfo,
        agent_server_url: str,
        working_dir: str,
        registered_marketplaces: list[MarketplaceRegistration] | None,
    ) -> None:
        """Let the agent-server clone Azure DevOps marketplaces with an Entra token.

        PATs ride in the authenticated source URL; Entra tokens cannot, so the
        repository credential helper is installed globally for each org instead.
        This relies on the agent-server running marketplace git commands with its
        full environment (the SDK's ``_run_git_subprocess``, not ``sanitized_env``).
        """
        orgs = await azure_devops_marketplace_orgs(
            registered_marketplaces, self.user_context
        )
        if not orgs or not await get_azure_devops_bearer_token(self.user_context):
            return

        web_url = getattr(self, 'web_url', None)
        helper_script = _azure_devops_credential_helper_script(web_url)
        commands = [
            'helper="$HOME/.openhands/azure-devops-credential-helper"',
            'mkdir -p "$HOME/.openhands"',
            f'printf %s {shlex.quote(helper_script)} > "$helper"',
            'chmod 700 "$helper"',
            'git config --global credential.https://dev.azure.com.useHttpPath true',
        ]
        for org in orgs:
            prefix = f'credential.https://dev.azure.com/{org}'
            commands += [
                f'git config --global {shlex.quote(prefix + ".username")} oauth2',
                'git config --global --replace-all '
                f'{shlex.quote(prefix + ".helper")} "!\\"$helper\\""',
            ]
        try:
            workspace = AsyncRemoteWorkspace(
                host=agent_server_url,
                api_key=sandbox.session_api_key,
                working_dir=working_dir,
            )
            try:
                result = await workspace.execute_command(' && '.join(commands), '/')
            finally:
                await workspace.reset_client()
            if result.exit_code:
                _logger.warning(
                    'Azure DevOps marketplace credential helper setup failed: '
                    f'{result.stderr}'
                )
        except Exception as e:
            _logger.warning(
                f'Azure DevOps marketplace credential helper setup failed: {e}'
            )

    async def maybe_run_setup_script(
        self,
        workspace: AsyncRemoteWorkspace,
        project_dir: str,
    ):
        """Run .openhands/setup.sh if it exists in the project root.

        Args:
            workspace: Remote workspace for command execution.
            project_dir: Project root directory (repo root when a repo is selected).
        """
        setup_script = project_dir + '/.openhands/setup.sh'

        await workspace.execute_command(
            f'chmod +x {setup_script} && source {setup_script}',
            cwd=project_dir,
            timeout=600,
        )

    async def maybe_setup_git_hooks(
        self,
        workspace: AsyncRemoteWorkspace,
        project_dir: str,
    ):
        """Set up git hooks if .openhands/pre-commit.sh exists in the project root.

        Args:
            workspace: Remote workspace for command execution.
            project_dir: Project root directory (repo root when a repo is selected).
        """
        command = 'mkdir -p .git/hooks && chmod +x .openhands/pre-commit.sh'
        pre_commit_command_result = await workspace.execute_command(
            command, project_dir
        )
        if pre_commit_command_result.exit_code:
            return

        # Check if there's an existing pre-commit hook
        with tempfile.TemporaryFile(mode='w+t') as temp_file:
            download_result = await workspace.file_download(
                PRE_COMMIT_HOOK, str(temp_file)
            )
            if download_result.success:
                _logger.info('Preserving existing pre-commit hook')
                # an existing pre-commit hook exists
                if 'This hook was installed by OpenHands' not in temp_file.read():
                    # Move the existing hook to pre-commit.local
                    command = (
                        f'mv {PRE_COMMIT_HOOK} {PRE_COMMIT_LOCAL} &&'
                        f'chmod +x {PRE_COMMIT_LOCAL}'
                    )
                    mv_chmod_result = await workspace.execute_command(
                        command, project_dir
                    )
                    if mv_chmod_result.exit_code != 0:
                        _logger.error(
                            f'Failed to preserve existing pre-commit hook: {mv_chmod_result.stderr}',
                        )
                        return

        # write the pre-commit hook
        await workspace.file_upload(
            source_path=Path(__file__).parent / 'git' / 'pre-commit.sh',
            destination_path=PRE_COMMIT_HOOK,
        )

        # Make the pre-commit hook executable
        chmod_result = await workspace.execute_command(f'chmod +x {PRE_COMMIT_HOOK}')
        if chmod_result.exit_code:
            _logger.error(
                f'Failed to make pre-commit hook executable: {chmod_result.stderr}'
            )
            return

        _logger.info('Git pre-commit hook installed successfully')

    async def maybe_inject_memory_context(
        self,
        workspace: AsyncRemoteWorkspace,
        project_dir: str,
    ) -> None:
        """Inject the user's stored memory context into the sandbox.

        If the user has ``enable_memory_context`` enabled and a non-empty
        ``memory_context`` on their user record, writes the content to
        ``<project_dir>/.openhands/memory/MEMORY.md`` in the sandbox so the
        SDK's ``load_memory()`` picks it up on the first
        ``send_message()``/``run()``.

        Args:
            workspace: Remote workspace for command execution.
            project_dir: Project root directory (repo root when a repo is selected).
        """
        try:
            user_info = await self.user_context.get_user_info()
        except Exception as e:
            _logger.warning(f'Failed to get user info for memory injection: {e}')
            return

        if not getattr(user_info, 'enable_memory_context', False):
            return

        memory_context = getattr(user_info, 'memory_context', None)
        if not memory_context:
            return

        memory_dir = f'{project_dir}/.openhands/memory'
        memory_file = f'{memory_dir}/MEMORY.md'

        # Create the memory directory and write the file content. The content
        # is base64-encoded to safely handle arbitrary text (quotes, special
        # chars, newlines) through a shell command.
        encoded = base64.b64encode(memory_context.encode('utf-8')).decode('ascii')
        command = (
            f'mkdir -p {memory_dir} && echo "{encoded}" | base64 -d > {memory_file}'
        )
        result = await workspace.execute_command(command, project_dir)
        if result.exit_code:
            _logger.error(f'Failed to write memory context to sandbox: {result.stderr}')
        else:
            _logger.info(
                'Injected memory_context (%d chars) into sandbox at %s',
                len(memory_context),
                memory_file,
            )

    def _create_condenser(
        self,
        llm: LLM,
        agent_type: AgentType,
        condenser_max_size: int | None,
    ) -> LLMSummarizingCondenser:
        """Create a condenser based on user settings and agent type.

        Args:
            llm: The LLM instance to use for condensation
            agent_type: Type of agent (PLAN or DEFAULT)
            condenser_max_size: condenser_max_size setting

        Returns:
            Configured LLMSummarizingCondenser instance
        """
        # LLMSummarizingCondenser SDK defaults: max_size=240, keep_first=2
        condenser_kwargs: dict[str, Any] = {
            'llm': llm.model_copy(
                update={
                    'usage_id': (
                        'condenser'
                        if agent_type == AgentType.DEFAULT
                        else 'planning_condenser'
                    )
                }
            ),
        }
        # Only override max_size if user has a custom value
        if condenser_max_size is not None:
            condenser_kwargs['max_size'] = condenser_max_size

        condenser = LLMSummarizingCondenser(**condenser_kwargs)

        return condenser

    def _create_security_analyzer_from_string(
        self, security_analyzer_str: str | None
    ) -> SecurityAnalyzerBase | None:
        """Convert security analyzer string from settings to SecurityAnalyzerBase instance.

        Args:
            security_analyzer_str: String value from settings. Valid values:
                - "llm" -> LLMSecurityAnalyzer
                - "none" or None -> None
                - Other values -> None (unsupported analyzers are ignored)

        Returns:
            SecurityAnalyzerBase instance or None
        """
        if not security_analyzer_str or security_analyzer_str.lower() == 'none':
            return None

        if security_analyzer_str.lower() == 'llm':
            return LLMSecurityAnalyzer()

        # For unknown values, log a warning and return None
        _logger.warning(
            f'Unknown security analyzer value: {security_analyzer_str}. '
            'Supported values: "llm", "none". Defaulting to None.'
        )
        return None

    def _select_confirmation_policy(
        self, confirmation_mode: bool, security_analyzer: str | None
    ) -> ConfirmationPolicyBase:
        """Choose confirmation policy using only mode flag and analyzer string."""
        if not confirmation_mode:
            return NeverConfirm()

        analyzer_kind = (security_analyzer or '').lower()
        if analyzer_kind == 'llm':
            return ConfirmRisky()

        return AlwaysConfirm()

    async def _set_security_analyzer_from_settings(
        self,
        agent_server_url: str,
        session_api_key: str | None,
        conversation_id: UUID,
        security_analyzer_str: str | None,
        httpx_client: 'httpx.AsyncClient',
    ) -> None:
        """Set security analyzer on conversation using only the analyzer string.

        Args:
            agent_server_url: URL of the agent server
            session_api_key: Session API key for authentication
            conversation_id: ID of the conversation to update
            security_analyzer_str: String value from settings
            httpx_client: HTTP client for making API requests
        """
        if session_api_key is None:
            return

        security_analyzer = self._create_security_analyzer_from_string(
            security_analyzer_str
        )

        # Only make API call if we have a security analyzer to set
        # (None is the default, so we can skip the call if it's None)
        if security_analyzer is None:
            return

        try:
            # Prepare the request payload
            payload = {'security_analyzer': security_analyzer.model_dump()}

            # Call agent server API to set security analyzer
            response = await httpx_client.post(
                f'{agent_server_url}/api/conversations/{conversation_id}/security_analyzer',
                json=payload,
                headers={'X-Session-API-Key': session_api_key},
                timeout=30.0,
            )
            response.raise_for_status()
            _logger.info(
                f'Successfully set security analyzer for conversation {conversation_id}'
            )
        except Exception as e:
            # Log error but don't fail conversation creation
            _logger.warning(
                f'Failed to set security analyzer for conversation {conversation_id}: {e}',
                exc_info=True,
            )
