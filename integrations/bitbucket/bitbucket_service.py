from collections.abc import Mapping

from pydantic import JsonValue, SecretStr, TypeAdapter

from integrations.native_git_mixin import NativeGitMixin, native_service_token
from openhands.app_server.integrations.bitbucket.bitbucket_service import (
    BitBucketService,
)
from openhands.app_server.integrations.service_types import ProviderType, RequestMethod
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.auth_config import ENABLE_KEYCLOAK
from server.auth.token_manager import TokenManager


class SaaSBitBucketService(NativeGitMixin, BitBucketService):
    def __init__(
        self,
        user_id: str | None = None,
        external_auth_token: SecretStr | None = None,
        external_auth_id: str | None = None,
        token: SecretStr | None = None,
        external_token_manager: bool = False,
        base_domain: str | None = None,
    ) -> None:
        logger.info(
            f'SaaSBitBucketService created with user_id {user_id}, external_auth_id {external_auth_id}, external_auth_token {"set" if external_auth_token else "None"}, bitbucket_token {"set" if token else "None"}, external_token_manager {external_token_manager}'
        )
        self._native_requested_host = base_domain
        if not ENABLE_KEYCLOAK:
            from server.auth.native_git_config import git_config

            base_domain = git_config('bitbucket', base_domain).host
        self.base_domain = base_domain
        super().__init__(
            user_id=user_id,
            external_auth_token=external_auth_token,
            external_auth_id=external_auth_id,
            token=token,
            external_token_manager=external_token_manager,
            base_domain=base_domain,
        )

        self.external_auth_token = external_auth_token
        self.external_auth_id = external_auth_id
        self.token_manager = TokenManager(external=external_token_manager)

    async def _legacy_headers(self) -> dict[str, str]:
        return TypeAdapter(dict[str, str]).validate_python(
            await BitBucketService._get_headers(self)
        )

    async def _legacy_request(
        self, url: str, params: Mapping[str, JsonValue] | None, method: RequestMethod
    ) -> tuple[JsonValue, dict[str, str]]:
        data, headers = await BitBucketService._make_request(
            self, url, dict(params) if params is not None else None, method
        )
        return TypeAdapter(JsonValue).validate_python(data), TypeAdapter(
            dict[str, str]
        ).validate_python(headers)

    async def get_latest_token(self) -> SecretStr | None:
        if not ENABLE_KEYCLOAK:
            return await native_service_token(self, ProviderType.BITBUCKET)
        bitbucket_token = None
        if self.external_auth_token:
            bitbucket_token = SecretStr(
                await self.token_manager.get_idp_token(
                    self.external_auth_token.get_secret_value(),
                    idp=ProviderType.BITBUCKET,
                )
            )
            logger.debug(
                f'Got BitBucket token {bitbucket_token} from access token: {self.external_auth_token}'
            )
        elif self.external_auth_id:
            offline_token = await self.token_manager.load_offline_token(
                self.external_auth_id
            )
            if offline_token:
                bitbucket_token_str: (
                    str | None
                ) = await self.token_manager.get_idp_token_from_offline_token(
                    offline_token, ProviderType.BITBUCKET
                )
                bitbucket_token = (
                    SecretStr(bitbucket_token_str) if bitbucket_token_str else None
                )
            else:
                bitbucket_token = None
            logger.info(
                f'Got BitBucket token {bitbucket_token} from external auth user ID: {self.external_auth_id}'
            )
        elif self.user_id:
            bitbucket_token_str = (
                await self.token_manager.get_idp_token_from_idp_user_id(
                    self.user_id, ProviderType.BITBUCKET
                )
            )
            bitbucket_token = (
                SecretStr(bitbucket_token_str) if bitbucket_token_str else None
            )
            logger.debug(
                f'Got BitBucket token {bitbucket_token} from user ID: {self.user_id}'
            )
        else:
            logger.warning('external_auth_token and user_id not set!')
        return bitbucket_token
