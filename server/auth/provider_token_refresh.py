"""Direct provider OAuth refresh; independent of application login sessions."""

import base64
import time
from urllib.parse import parse_qs

import httpx

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.http_session import httpx_verify_option
from server.auth.constants import (
    AZURE_DEVOPS_CLIENT_ID,
    AZURE_DEVOPS_CLIENT_SECRET,
    AZURE_DEVOPS_SCOPE,
    AZURE_DEVOPS_TENANT_ID,
    AZURE_DEVOPS_TOKEN_URL,
    BITBUCKET_APP_CLIENT_ID,
    BITBUCKET_APP_CLIENT_SECRET,
    BITBUCKET_DATA_CENTER_CLIENT_ID,
    BITBUCKET_DATA_CENTER_CLIENT_SECRET,
    BITBUCKET_DATA_CENTER_HOST,
    BITBUCKET_DATA_CENTER_TOKEN_URL,
    GITHUB_APP_CLIENT_ID,
    GITHUB_APP_CLIENT_SECRET,
    GITLAB_APP_CLIENT_ID,
    GITLAB_APP_CLIENT_SECRET,
    GITLAB_TOKEN_URL,
)
from server.auth.contracts import AuthenticationUnavailable, ProviderReconnectRequired
from server.logger import logger

IDP_HTTP_TIMEOUT = 15.0


class ProviderTokenRefresher:
    def __init__(self, jwt_service: JwtService):
        self._jwt_svc = jwt_service

    def encrypt_text(self, value: str) -> str:
        return self._jwt_svc.encrypt_value(value)

    def decrypt_text(self, value: str) -> str:
        return self._jwt_svc.decrypt_value(value)

    async def _check_expiration_and_refresh(
        self,
        identity_provider: ProviderType,
        encrypted_refresh_token: str,
        access_token_expires_at: int,
        refresh_token_expires_at: int,
    ) -> dict[str, str | int] | None:
        current_time = int(time.time())
        # Refresh access tokens before expiration to ensure validity on resume.
        # Azure DevOps uses a shorter buffer because Entra access tokens are
        # short-lived; other providers keep the existing 4-hour buffer.
        access_token_refresh_buffer_seconds = (
            300 if identity_provider == ProviderType.AZURE_DEVOPS else 14400
        )
        access_expired = (
            False
            if access_token_expires_at == 0
            else access_token_expires_at
            < current_time + access_token_refresh_buffer_seconds
        )
        refresh_expired = (
            False
            if refresh_token_expires_at == 0
            else refresh_token_expires_at < current_time
        )

        if not access_expired:
            return None
        if access_expired and refresh_expired:
            logger.error('Both Access and Refresh Tokens expired.')
            raise ProviderReconnectRequired(
                'Provider credentials have expired. Reconnect the provider.'
            )

        logger.info(f'Access token expired for {identity_provider}. Refreshing token.')
        refresh_token = self.decrypt_text(encrypted_refresh_token)
        token_data = await self._refresh_token(identity_provider, refresh_token)
        access_token = str(token_data['access_token'])
        refresh_token = str(token_data['refresh_token'])
        access_expiration = token_data['access_token_expires_at']
        refresh_expiration = token_data['refresh_token_expires_at']

        return {
            'access_token': self.encrypt_text(access_token),
            'refresh_token': self.encrypt_text(refresh_token),
            'access_token_expires_at': access_expiration,
            'refresh_token_expires_at': refresh_expiration,
        }

    async def _refresh_token(
        self, idp: ProviderType, refresh_token: str
    ) -> dict[str, str | int]:
        logger.info(f'Refreshing {idp} token')
        if idp == ProviderType.GITHUB:
            return await self._refresh_github_token(refresh_token)
        elif idp == ProviderType.GITLAB:
            return await self._refresh_gitlab_token(refresh_token)
        elif idp == ProviderType.BITBUCKET:
            return await self._refresh_bitbucket_token(refresh_token)
        elif idp == ProviderType.BITBUCKET_DATA_CENTER:
            return await self._refresh_bitbucket_data_center_token(refresh_token)
        elif idp == ProviderType.AZURE_DEVOPS:
            return await self._refresh_azure_devops_token(refresh_token)
        else:
            raise ValueError(f'Unsupported IDP: {idp}')

    async def _refresh_github_token(self, refresh_token: str) -> dict[str, str | int]:
        url = 'https://github.com/login/oauth/access_token'
        logger.info(f'Refreshing GitHub token with URL: {url}')

        payload = {
            'client_id': GITHUB_APP_CLIENT_ID,
            'client_secret': GITHUB_APP_CLIENT_SECRET,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        }
        async with httpx.AsyncClient(
            verify=httpx_verify_option(), timeout=IDP_HTTP_TIMEOUT
        ) as client:
            response = await client.post(url, data=payload)
            response.raise_for_status()
            logger.info('Successfully refreshed GitHub token')
            parsed = parse_qs(response.text)

            # Convert lists to strings and specific keys to integers
            data = {
                key: int(value[0])
                if key
                in {'expires_in', 'refresh_token_expires_in', 'refresh_expires_in'}
                else value[0]
                for key, value in parsed.items()
            }
            data.setdefault('refresh_token', refresh_token)
            return await self._parse_refresh_response(data)

    async def _refresh_gitlab_token(self, refresh_token: str) -> dict[str, str | int]:
        url = GITLAB_TOKEN_URL
        logger.info(f'Refreshing GitLab token with URL: {url}')

        payload = {
            'client_id': GITLAB_APP_CLIENT_ID,
            'client_secret': GITLAB_APP_CLIENT_SECRET,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        }
        async with httpx.AsyncClient(
            verify=httpx_verify_option(), timeout=IDP_HTTP_TIMEOUT
        ) as client:
            response = await client.post(url, data=payload)
            response.raise_for_status()
            logger.info('Successfully refreshed GitLab token')

            data = response.json()
            data.setdefault('refresh_token', refresh_token)
            return await self._parse_refresh_response(data)

    async def _refresh_bitbucket_token(
        self, refresh_token: str
    ) -> dict[str, str | int]:
        url = 'https://bitbucket.org/site/oauth2/access_token'
        logger.info(f'Refreshing Bitbucket token with URL: {url}')

        auth = base64.b64encode(
            f'{BITBUCKET_APP_CLIENT_ID}:{BITBUCKET_APP_CLIENT_SECRET}'.encode()
        ).decode()

        headers = {
            'Authorization': f'Basic {auth}',
            'Content-Type': 'application/x-www-form-urlencoded',
        }

        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        }

        async with httpx.AsyncClient(
            verify=httpx_verify_option(), timeout=IDP_HTTP_TIMEOUT
        ) as client:
            response = await client.post(url, data=data, headers=headers)
            response.raise_for_status()
            logger.info('Successfully refreshed Bitbucket token')

            data = response.json()
            data.setdefault('refresh_token', refresh_token)
            return await self._parse_refresh_response(data)

    async def _refresh_bitbucket_data_center_token(
        self, refresh_token: str
    ) -> dict[str, str | int]:
        if not BITBUCKET_DATA_CENTER_HOST:
            raise ValueError(
                'BITBUCKET_DATA_CENTER_HOST is not configured. '
                'Set the BITBUCKET_DATA_CENTER_HOST environment variable.'
            )
        url = BITBUCKET_DATA_CENTER_TOKEN_URL
        logger.info(f'Refreshing Bitbucket Data Center token with URL: {url}')

        payload = {
            'client_id': BITBUCKET_DATA_CENTER_CLIENT_ID,
            'client_secret': BITBUCKET_DATA_CENTER_CLIENT_SECRET,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        }
        async with httpx.AsyncClient(
            verify=httpx_verify_option(), timeout=IDP_HTTP_TIMEOUT
        ) as client:
            response = await client.post(url, data=payload)
            response.raise_for_status()
            logger.info('Successfully refreshed Bitbucket Data Center token')

            data = response.json()
            data.setdefault('refresh_token', refresh_token)
            return await self._parse_refresh_response(data)

    async def _refresh_azure_devops_token(
        self, refresh_token: str
    ) -> dict[str, str | int]:
        if (
            not AZURE_DEVOPS_TENANT_ID
            or not AZURE_DEVOPS_CLIENT_ID
            or not AZURE_DEVOPS_CLIENT_SECRET
        ):
            raise ValueError(
                'Azure DevOps OAuth is not configured. Set AZURE_DEVOPS_TENANT_ID, '
                'AZURE_DEVOPS_CLIENT_ID, and AZURE_DEVOPS_CLIENT_SECRET.'
            )

        logger.info(f'Refreshing Azure DevOps token with URL: {AZURE_DEVOPS_TOKEN_URL}')
        payload = {
            'client_id': AZURE_DEVOPS_CLIENT_ID,
            'client_secret': AZURE_DEVOPS_CLIENT_SECRET,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
            'scope': AZURE_DEVOPS_SCOPE,
        }
        async with httpx.AsyncClient(
            verify=httpx_verify_option(), timeout=IDP_HTTP_TIMEOUT
        ) as client:
            response = await client.post(AZURE_DEVOPS_TOKEN_URL, data=payload)
            response.raise_for_status()
            logger.info('Successfully refreshed Azure DevOps token')

            data = response.json()
            data.setdefault('refresh_token', refresh_token)
            return await self._parse_refresh_response(data)

    async def _parse_refresh_response(self, data: dict) -> dict[str, str | int]:
        access_token = data.get('access_token')
        refresh_token = data.get('refresh_token')
        if not access_token or not refresh_token:
            if data.get('error') in (
                'bad_refresh_token',
                'invalid_grant',
                'invalid_token',
            ):
                raise ProviderReconnectRequired(
                    'Provider credentials were revoked. Reconnect the provider.'
                )
            raise AuthenticationUnavailable(
                'Provider returned an incomplete token refresh response.'
            )

        expires_in = int(data.get('expires_in', 0))
        refresh_expires_in = int(
            data.get('refresh_token_expires_in', data.get('refresh_expires_in', 0))
        )
        current_time = int(time.time())
        access_token_expires_at = 0 if expires_in == 0 else current_time + expires_in
        refresh_token_expires_at = (
            0 if refresh_expires_in == 0 else current_time + refresh_expires_in
        )

        logger.info(
            f'Token refresh successful. New access token expires at: {access_token_expires_at}, refresh token expires at: {refresh_token_expires_at}'
        )
        return {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'access_token_expires_at': access_token_expires_at,
            'refresh_token_expires_at': refresh_token_expires_at,
        }
