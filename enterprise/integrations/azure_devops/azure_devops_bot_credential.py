"""Bot posting-identity credentials for Azure DevOps integrations.

When a bot credential is configured, the resolver posts its comments/reactions
as the bot instead of the @-mentioning user; the agent job still runs with the
mentioner's own token (unchanged authz). Phase 1 supports a PAT via HTTP Basic
auth; the abstraction leaves room for service-principal / managed-identity
credentials later. Opt-in: with no bot token we post as the mentioner.
"""

from abc import ABC, abstractmethod

from integrations.azure_devops.azure_devops_service import SaaSAzureDevOpsService
from pydantic import SecretStr
from server.auth.constants import AZURE_DEVOPS_BOT_TOKEN


class AdoBotCredential(ABC):
    """A credential that authenticates the Azure DevOps bot posting identity."""

    @abstractmethod
    def build_service(self) -> SaaSAzureDevOpsService:
        """Return a service authenticated as the bot (no per-user token)."""


class AdoPatBotCredential(AdoBotCredential):
    """Authenticate the bot via a PAT (HTTP Basic, ``:<pat>``)."""

    def __init__(self, pat: str):
        self._pat = pat

    def build_service(self) -> SaaSAzureDevOpsService:
        # The base service detects a non-JWT token and uses Basic auth; set the
        # raw PAT directly and disable refresh (a static PAT never rotates here).
        service = SaaSAzureDevOpsService()
        service.token = SecretStr(self._pat)
        service.refresh = False
        return service


def get_azure_devops_bot_credential() -> AdoBotCredential | None:
    """Return the configured bot credential, or None to post as the mentioner."""
    if AZURE_DEVOPS_BOT_TOKEN:
        return AdoPatBotCredential(AZURE_DEVOPS_BOT_TOKEN)
    return None
