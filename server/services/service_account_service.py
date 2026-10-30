"""Service accounts: non-human members of one organization.

A service account is a ``user`` row with ``account_type = 'service'`` and one
``org_member`` row with the ``service_account`` role, in the org that owns it.
It has no personal org, email, password or super role, so it never signs in;
it acts through API keys. Deleting the org deletes it
(``OrgStore.delete_org_cascade``).
"""

from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from openhands.app_server.settings.settings_models import agent_llm
from openhands.app_server.utils.logger import openhands_logger as logger
from server.constants import ROLE_SERVICE_ACCOUNT
from server.routes.org_models import LiteLLMIntegrationError, OrgNotFoundError
from storage.lite_llm_manager import LiteLlmManager
from storage.org_store import OrgStore
from storage.role_store import RoleStore
from storage.user import User
from storage.user_store import UserStore


class ServiceAccountNameExistsError(Exception):
    """Raised when the org already has a service account with the name."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f'A service account named "{name}" already exists')


class ServiceAccountService:
    """Service account operations. Callers are responsible for authorization."""

    @staticmethod
    async def create_service_account(org_id: UUID, name: str) -> User:
        """Create a service account in ``org_id`` and return its user.

        Creates its LiteLLM user and key in the org's team first, then the
        ``user`` and ``org_member`` rows in one transaction. If the rows
        cannot be written, the LiteLLM user is removed best effort.

        Raises:
            ValueError: If ``name`` is blank.
            OrgNotFoundError: If the org does not exist.
            ServiceAccountNameExistsError: If the org already has a service
                account with this name, ignoring case.
            LiteLLMIntegrationError: If LiteLLM provisioning fails.
        """
        name = name.strip()
        if not name:
            raise ValueError('Service account name is required')
        if await OrgStore.get_org_by_id(org_id) is None:
            raise OrgNotFoundError(str(org_id))
        role = await RoleStore.get_role_by_name(ROLE_SERVICE_ACCOUNT)
        if role is None:
            raise RuntimeError(f'Role {ROLE_SERVICE_ACCOUNT!r} not found in database')

        user_id = uuid4()
        # create_user=True: no LiteLLM user exists yet for this new id.
        settings = await UserStore.create_default_settings(
            org_id=str(org_id), user_id=str(user_id), create_user=True
        )
        if settings is None:
            raise LiteLLMIntegrationError('Failed to create LiteLLM settings')
        llm_api_key = getattr(agent_llm(settings.agent_settings), 'api_key', None)
        if isinstance(llm_api_key, SecretStr):
            llm_api_key = llm_api_key.get_secret_value()

        try:
            user = await UserStore.create_service_account(
                org_id=org_id,
                user_id=user_id,
                name=name,
                role_id=role.id,
                llm_api_key=llm_api_key or '',
            )
        except Exception as exc:
            await ServiceAccountService._delete_litellm_user_best_effort(user_id)
            if isinstance(exc, IntegrityError) and 'uq_user_service_account_name' in (
                str(exc)
            ):
                raise ServiceAccountNameExistsError(name) from exc
            raise

        logger.info(
            'service_account:created',
            extra={'org_id': str(org_id), 'service_account_id': str(user_id)},
        )
        return user

    @staticmethod
    async def _delete_litellm_user_best_effort(user_id: UUID) -> None:
        try:
            await LiteLlmManager.delete_user(str(user_id))
        except Exception as exc:
            logger.warning(
                'service_account:litellm_cleanup_failed',
                extra={'service_account_id': str(user_id), 'error': str(exc)},
            )
