"""Native provider adapter shared by the existing SaaS Git services."""

from abc import abstractmethod
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx
from pydantic import JsonValue, SecretStr, TypeAdapter

from openhands.app_server.integrations.protocols.http_client import HTTPClient
from openhands.app_server.integrations.service_types import (
    ProviderType,
    RequestMethod,
)
from openhands.app_server.utils.http_session import httpx_verify_option
from server.auth.auth_config import ENABLE_KEYCLOAK
from server.auth.native_git_config import NativeGitConfig, git_config
from server.services.native_git_credentials import get_native_git_service
from server.services.native_git_provider import GitCredentialError


async def native_service_token(
    service: 'NativeGitMixin', provider: ProviderType
) -> SecretStr | None:
    resolver = get_native_git_service()
    account_id = service.external_auth_id
    if not account_id and service.user_id:
        config = git_config(provider.value, service.base_domain)
        account_id = await resolver.resolve_actor(
            provider, config.host, str(service.user_id)
        )
        if not account_id:
            raise GitCredentialError('provider_actor_unmapped', 403)
    if account_id:
        credential = await resolver.get_token(account_id, provider)
        requested = service._native_requested_host
        if requested and credential.host != git_config(provider.value, requested).host:
            raise GitCredentialError('provider_host_mismatch', 403)
        config = git_config(provider.value, credential.host)
        service._configure_native_urls(config)
        service._native_account_id = account_id
        if credential.token is None:
            raise GitCredentialError('credential_rejected')
        service.token = credential.token
        return credential.token
    # Explicit provider tokens are used by verified installation/bot contexts.
    # Never interpret an application auth token as a provider credential.
    if service.external_auth_token:
        raise GitCredentialError('native_provider_context_required', 403)
    return service.token


class NativeGitMixin(HTTPClient):
    BASE_URL: str
    user_id: str | None
    _native_requested_host: str | None = None
    _native_account_id: str | None = None

    def _configure_native_urls(self, config: NativeGitConfig) -> None:
        self.base_domain = config.host
        self.BASE_URL = config.api_url

    @abstractmethod
    async def _legacy_headers(self) -> dict[str, str]: ...

    @abstractmethod
    async def _legacy_request(
        self, url: str, params: Mapping[str, JsonValue] | None, method: RequestMethod
    ) -> tuple[JsonValue, dict[str, str]]: ...

    supports_native_auth = True

    async def _get_headers(self) -> dict[str, str]:
        if not ENABLE_KEYCLOAK:
            await self.get_latest_token()
        return await self._legacy_headers()

    async def _make_request(
        self,
        url: str,
        params: Mapping[str, JsonValue] | None = None,
        method: RequestMethod = RequestMethod.GET,
    ) -> tuple[JsonValue, dict[str, str]]:
        if ENABLE_KEYCLOAK:
            return await self._legacy_request(url, params, method)
        previous_api = self.BASE_URL
        token = await self.get_latest_token()
        provider = self.provider
        config = git_config(provider, self.base_domain)
        if not self._native_requested_host and url.startswith(previous_api + '/'):
            url = config.api_url + url[len(previous_api) :]
        target = urlsplit(url)
        allowed = urlsplit(config.api_url)
        if (
            target.scheme != 'https'
            or target.netloc != allowed.netloc
            or target.username
            or target.password
        ):
            raise GitCredentialError('provider_host_mismatch', 403)
        # Capture one credential+host pair for the entire HTTP operation. A
        # concurrent reconnect cannot combine a new host's token with an old URL.
        value = token.get_secret_value() if token else ''
        authorization = f'Bearer {value}'
        headers = {'Authorization': authorization, 'Accept': 'application/json'}
        try:
            async with httpx.AsyncClient(
                timeout=15, follow_redirects=False, verify=httpx_verify_option()
            ) as client:
                response = await self.execute_request(
                    client, url, headers, params, method
                )
            if response.status_code == 401:
                account_id = self._native_account_id
                if account_id:
                    await get_native_git_service().mark_rejected_by_authorization(
                        account_id, provider, authorization
                    )
                raise GitCredentialError('credential_rejected')
            if response.status_code == 403 and (
                response.headers.get('Retry-After')
                or response.headers.get('X-RateLimit-Remaining') == '0'
            ):
                raise GitCredentialError('provider_unavailable', 503)
            if response.status_code == 429 or response.status_code >= 500:
                raise GitCredentialError('provider_unavailable', 503)
            response.raise_for_status()
            try:
                data = (
                    TypeAdapter(JsonValue).validate_json(response.content)
                    if 'json' in response.headers.get('Content-Type', '')
                    else response.text
                )
            except ValueError:
                raise GitCredentialError('provider_response_invalid', 502) from None
            response_headers = dict(response.headers)
            for name in ('Link', 'X-Total'):
                if name in response.headers:
                    response_headers[name] = response.headers[name]
            return data, response_headers
        except httpx.HTTPStatusError as exc:
            raise self.handle_http_status_error(exc) from exc
        except httpx.HTTPError as exc:
            raise GitCredentialError('provider_unavailable', 503) from exc
