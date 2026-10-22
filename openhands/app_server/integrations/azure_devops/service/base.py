from abc import abstractmethod
from typing import Any
from urllib.parse import quote

from pydantic import SecretStr

from openhands.app_server.integrations.protocols.http_client import HTTPClient
from openhands.app_server.integrations.service_types import (
    BaseGitService,
    RequestMethod,
    UnknownException,
)

# Azure error messages are short; cap what we log and show to users.
_MAX_AZURE_ERROR_MESSAGE_LENGTH = 500


class AzureDevOpsApiError(UnknownException):
    """Azure DevOps rejected a request; keeps the reason Azure gave."""

    def __init__(
        self,
        status_code: int,
        azure_message: str | None,
        type_key: str | None = None,
    ):
        self.status_code = status_code
        self.azure_message = azure_message
        self.type_key = type_key
        reason = azure_message or 'no error message returned'
        super().__init__(f'Azure DevOps API error {status_code}: {reason}')


def azure_error_details(response: Any) -> tuple[str | None, str | None]:
    """Return (message, typeKey) from an Azure DevOps error response body."""
    try:
        body = response.json()
    except Exception:
        body = None
    if isinstance(body, dict):
        message = body.get('message')
        type_key = body.get('typeKey')
    else:
        message = (getattr(response, 'text', '') or '').strip() or None
        type_key = None
    if message is not None:
        message = str(message)[:_MAX_AZURE_ERROR_MESSAGE_LENGTH]
    return message, str(type_key) if type_key else None


class AzureDevOpsMixinBase(BaseGitService, HTTPClient):
    """Declares common attributes and method signatures used across Azure DevOps mixins."""

    organization: str

    @property
    @abstractmethod
    def base_url(self) -> str:
        """Get the base URL for Azure DevOps API calls."""
        ...

    async def _get_headers(self) -> dict:
        """Retrieve the Azure DevOps token from settings store to construct the headers."""
        if not self.token:
            latest_token = await self.get_latest_token()
            if latest_token:
                self.token = latest_token

        return {
            'Authorization': f'Bearer {self.token.get_secret_value() if self.token else ""}',
            'Content-Type': 'application/json',
        }

    async def get_latest_token(self) -> SecretStr | None:  # type: ignore[override]
        return self.token

    async def _make_request(
        self,
        url: str,
        params: dict | None = None,
        method: RequestMethod = RequestMethod.GET,
    ) -> tuple[Any, dict]:  # type: ignore[override]
        """Make HTTP request to Azure DevOps API."""
        raise NotImplementedError('Implemented in AzureDevOpsServiceImpl')

    def _parse_repository(self, repository: str) -> tuple[str, str, str]:
        """Parse repository string into organization, project, and repo name."""
        raise NotImplementedError('Implemented in AzureDevOpsServiceImpl')

    def _truncate_comment(self, comment: str, max_length: int = 1000) -> str:
        """Truncate comment to max length."""
        if len(comment) <= max_length:
            return comment
        return comment[:max_length] + '...'

    @staticmethod
    def _encode_url_component(component: str) -> str:
        """URL-encode a component for use in Azure DevOps API URLs.

        Args:
            component: The string component to encode (e.g., repo name, project name, org name)

        Returns:
            URL-encoded string with spaces and special characters properly encoded
        """
        return quote(component, safe='')

    def _project_base_url(self, org: str, project: str) -> str:
        org_enc = self._encode_url_component(org)
        project_enc = self._encode_url_component(project)
        return f'https://dev.azure.com/{org_enc}/{project_enc}'
