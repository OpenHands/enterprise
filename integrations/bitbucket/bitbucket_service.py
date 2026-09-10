from pydantic import SecretStr

from integrations.provider_service import ProviderCredentialErrorMixin
from openhands.app_server.integrations.bitbucket.bitbucket_service import (
    BitBucketService,
)
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.provider_credentials import ProviderCredentialService


class SaaSBitBucketService(ProviderCredentialErrorMixin, BitBucketService):
    def __init__(
        self,
        user_id: str | None = None,
        external_auth_token: SecretStr | None = None,
        external_auth_id: str | None = None,
        token: SecretStr | None = None,
        external_token_manager: bool = False,
        base_domain: str | None = None,
    ):
        logger.info(
            f'SaaSBitBucketService created with user_id {user_id}, external_auth_id {external_auth_id}, external_auth_token {"set" if external_auth_token else "None"}, bitbucket_token {"set" if token else "None"}, external_token_manager {external_token_manager}'
        )
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
        self.provider_credentials = ProviderCredentialService()
        self._credential_host = base_domain

    async def get_latest_token(self) -> SecretStr | None:
        token = await self.provider_credentials.token_for_service(
            ProviderType.BITBUCKET,
            user_id=self.external_auth_id,
            account_id=self.user_id,
            access_token=self.external_auth_token,
            host=self._credential_host,
        )
        if token:
            self.token = token
        return token
