from pydantic import SecretStr

from openhands.app_server.integrations.bitbucket.bitbucket_service import (
    BitBucketService,
)
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.token_manager import TokenManager


class SaaSBitBucketService(BitBucketService):
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
        self.token_manager = TokenManager(external=external_token_manager)
        # On a 401 the base class calls get_latest_token() again and retries.
        self.refresh = True

    async def get_latest_token(self) -> SecretStr | None:
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
            # A token already in hand means the provider just refused it.
            bitbucket_token_str = await self.token_manager.get_idp_token_for_user(
                self.external_auth_id,
                ProviderType.BITBUCKET,
                rejected=self.token.get_secret_value() or None,
            )
            bitbucket_token = (
                SecretStr(bitbucket_token_str) if bitbucket_token_str else None
            )
            if bitbucket_token:
                self.token = bitbucket_token  # the retry reads self.token
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
