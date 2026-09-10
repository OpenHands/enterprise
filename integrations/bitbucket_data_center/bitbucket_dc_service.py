from pydantic import SecretStr

from integrations.provider_service import ProviderCredentialErrorMixin
from openhands.app_server.integrations.bitbucket_data_center.bitbucket_dc_service import (
    BitbucketDCService,
)
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.provider_credentials import ProviderCredentialService


class SaaSBitbucketDCService(ProviderCredentialErrorMixin, BitbucketDCService):
    def __init__(
        self,
        user_id: str | None = None,
        external_auth_token: SecretStr | None = None,
        external_auth_id: str | None = None,
        token: SecretStr | None = None,
        external_token_manager: bool = False,
        base_domain: str | None = None,
    ):
        logger.debug(
            f'SaaSBitbucketDCService created with user_id {user_id}, external_auth_id {external_auth_id}, external_auth_token {"set" if external_auth_token else "None"}, token {"set" if token else "None"}, external_token_manager {external_token_manager}'
        )
        super().__init__(
            user_id=user_id,
            external_auth_token=external_auth_token,
            external_auth_id=external_auth_id,
            token=token,
            external_token_manager=external_token_manager,
            base_domain=base_domain,
        )

        self.provider_credentials = ProviderCredentialService()
        self._credential_host = base_domain
        self.refresh = True

    async def get_latest_token(self) -> SecretStr | None:
        token = await self.provider_credentials.token_for_service(
            ProviderType.BITBUCKET_DATA_CENTER,
            user_id=self.external_auth_id,
            account_id=self.user_id,
            access_token=self.external_auth_token,
            host=self._credential_host,
        )
        if token:
            self.token = token
        return token

    async def get_user(self):
        # ProviderToken.user_id is the stable numeric account ID, while the
        # BBDC /users filter accepts a username. Resolve the authenticated
        # username instead of treating the persisted account ID as a name.
        return await self.provider_credentials._validate_bitbucket_dc(self)
