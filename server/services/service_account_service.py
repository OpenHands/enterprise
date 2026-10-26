"""Service accounts: non-human members of one organization.

A service account is a ``user`` row with ``account_type = 'service'`` and one
``org_member`` row with the ``service_account`` role, in the org that owns it.
It has no personal org, email, password or super role, so it never signs in;
it acts through API keys. Deleting the org deletes it
(``OrgStore.delete_org_cascade``).
"""

from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from openhands.app_server.settings.settings_models import agent_llm
from openhands.app_server.utils.logger import openhands_logger as logger
from server.constants import ROLE_SERVICE_ACCOUNT
from server.routes.org_models import (
    LiteLLMIntegrationError,
    OrgNotFoundError,
    ServiceAccountResponse,
)
from storage.database import a_session_maker
from storage.lite_llm_manager import LiteLlmManager
from storage.org_member import OrgMember
from storage.org_store import OrgStore
from storage.role import Role
from storage.role_store import RoleStore
from storage.user import ACCOUNT_TYPE_SERVICE, User
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
    def _select_in_org(org_id: UUID):
        """Select ``(User, role name)`` for the service accounts of ``org_id``.

        A user that is not a service account, or not a member of ``org_id``,
        is never selected. Every lookup below goes through this query.
        """
        return (
            select(User, Role.name)
            .join(OrgMember, OrgMember.user_id == User.id)
            .join(Role, Role.id == OrgMember.role_id)
            .where(
                OrgMember.org_id == org_id,
                User.account_type == ACCOUNT_TYPE_SERVICE,
            )
        )

    @staticmethod
    def _to_response(
        org_id: UUID, user: User, role_name: str
    ) -> ServiceAccountResponse:
        return ServiceAccountResponse(
            id=str(user.id),
            org_id=str(org_id),
            name=user.display_name or '',
            is_disabled=user.is_disabled,
            role=role_name,
        )

    @staticmethod
    async def get_service_account(
        org_id: UUID, service_account_id: UUID
    ) -> ServiceAccountResponse | None:
        """Return the service account, or ``None`` if ``service_account_id``
        is not a service account that is a member of ``org_id``."""
        async with a_session_maker() as session:
            row = (
                await session.execute(
                    ServiceAccountService._select_in_org(org_id).where(
                        User.id == service_account_id
                    )
                )
            ).first()
        return ServiceAccountService._to_response(org_id, *row) if row else None

    @staticmethod
    async def list_service_accounts(
        org_id: UUID, offset: int, limit: int
    ) -> list[ServiceAccountResponse]:
        """Return up to ``limit`` service accounts of ``org_id`` by name."""
        async with a_session_maker() as session:
            rows = (
                await session.execute(
                    ServiceAccountService._select_in_org(org_id)
                    .order_by(func.lower(User.display_name), User.id)
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        return [ServiceAccountService._to_response(org_id, *row) for row in rows]

    @staticmethod
    async def rename_service_account(
        org_id: UUID, service_account_id: UUID, name: str
    ) -> ServiceAccountResponse | None:
        """Rename the service account. Returns ``None`` if it is not one of ``org_id``.

        Raises:
            ValueError: If ``name`` is blank.
            ServiceAccountNameExistsError: If another service account of the
                org has this name, ignoring case.
        """
        name = name.strip()
        if not name:
            raise ValueError('Service account name is required')
        async with a_session_maker() as session:
            row = (
                await session.execute(
                    ServiceAccountService._select_in_org(org_id)
                    .where(User.id == service_account_id)
                    .with_for_update(of=User)
                )
            ).first()
            if row is None:
                return None
            user, role_name = row
            user.display_name = name
            try:
                await session.commit()
            except IntegrityError as exc:
                if 'uq_user_service_account_name' in str(exc):
                    raise ServiceAccountNameExistsError(name) from exc
                raise
        return ServiceAccountService._to_response(org_id, user, role_name)

    @staticmethod
    async def _delete_litellm_user_best_effort(user_id: UUID) -> None:
        try:
            await LiteLlmManager.delete_user(str(user_id))
        except Exception as exc:
            logger.warning(
                'service_account:litellm_cleanup_failed',
                extra={'service_account_id': str(user_id), 'error': str(exc)},
            )
