from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError

from openhands.app_server.utils.logger import openhands_logger as logger
from storage.database import a_session_maker
from storage.jira_conversation import JiraConversation
from storage.jira_user import JiraUser
from storage.jira_workspace import JiraWorkspace


def workspace_visible_to_org(
    workspace: JiraWorkspace, effective_org_id: UUID | None
) -> bool:
    """Whether an active Jira workspace should be visible to a user in the
    given org. Install-wide when it has no org or is stamped with its creator's
    personal org (org_id == admin_user_id); otherwise scoped to that org.
    Mirrors the Jira DC predicate so status/hint surfaces agree on who can see
    an org's Jira Cloud connection.
    """
    org_id = getattr(workspace, 'org_id', None)
    if org_id is None:
        return True
    admin_user_id = getattr(workspace, 'admin_user_id', None)
    if admin_user_id is not None and str(org_id) == str(admin_user_id):
        return True
    return effective_org_id is not None and str(org_id) == str(effective_org_id)


@dataclass
class JiraIntegrationStore:
    async def create_workspace(
        self,
        name: str,
        jira_cloud_id: str,
        admin_user_id: str,
        org_id: UUID | None,
        encrypted_webhook_secret: str,
        svc_acc_email: str,
        encrypted_svc_acc_api_key: str,
        status: str = 'active',
    ) -> JiraWorkspace:
        """Create a new Jira workspace with encrypted sensitive data."""

        workspace = JiraWorkspace(
            name=name.lower(),
            jira_cloud_id=jira_cloud_id,
            admin_user_id=admin_user_id,
            org_id=org_id,
            webhook_secret=encrypted_webhook_secret,
            svc_acc_email=svc_acc_email,
            svc_acc_api_key=encrypted_svc_acc_api_key,
            status=status,
        )

        async with a_session_maker() as session:
            session.add(workspace)
            await session.commit()
            await session.refresh(workspace)

        logger.info(f'[Jira] Created workspace {workspace.name}')
        return workspace

    async def update_workspace(
        self,
        id: int,
        org_id: UUID | None = None,
        jira_cloud_id: Optional[str] = None,
        encrypted_webhook_secret: Optional[str] = None,
        svc_acc_email: Optional[str] = None,
        encrypted_svc_acc_api_key: Optional[str] = None,
        status: Optional[str] = None,
    ) -> JiraWorkspace:
        """Update an existing Jira workspace with encrypted sensitive data."""
        async with a_session_maker() as session:
            # Find existing workspace by ID
            result = await session.execute(
                select(JiraWorkspace).filter(JiraWorkspace.id == id)
            )
            workspace = result.scalars().first()

            if not workspace:
                raise ValueError(f'Workspace with ID "{id}" not found')

            if org_id is not None:
                workspace.org_id = org_id

            if jira_cloud_id is not None:
                workspace.jira_cloud_id = jira_cloud_id

            if encrypted_webhook_secret is not None:
                workspace.webhook_secret = encrypted_webhook_secret

            if svc_acc_email is not None:
                workspace.svc_acc_email = svc_acc_email

            if encrypted_svc_acc_api_key is not None:
                workspace.svc_acc_api_key = encrypted_svc_acc_api_key

            if status is not None:
                workspace.status = status

            await session.commit()
            await session.refresh(workspace)

            logger.info(f'[Jira] Updated workspace {workspace.name}')
            return workspace

    async def create_workspace_link(
        self,
        keycloak_user_id: str,
        jira_user_id: str,
        jira_workspace_id: int,
        status: str = 'active',
    ) -> JiraUser:
        """Create a new Jira workspace link."""

        jira_user = JiraUser(
            keycloak_user_id=keycloak_user_id,
            jira_user_id=jira_user_id,
            jira_workspace_id=jira_workspace_id,
            status=status,
        )

        async with a_session_maker() as session:
            session.add(jira_user)
            await session.commit()
            await session.refresh(jira_user)

        logger.info(
            f'[Jira] Created user {jira_user.id} for workspace {jira_workspace_id}'
        )
        return jira_user

    async def get_workspace_by_id(self, workspace_id: int) -> Optional[JiraWorkspace]:
        """Retrieve workspace by ID."""
        async with a_session_maker() as session:
            result = await session.execute(
                select(JiraWorkspace).filter(JiraWorkspace.id == workspace_id)
            )
            return result.scalars().first()

    async def get_workspace_by_name(self, workspace_name: str) -> JiraWorkspace | None:
        """Retrieve workspace by name."""
        async with a_session_maker() as session:
            result = await session.execute(
                select(JiraWorkspace).filter(
                    JiraWorkspace.name == workspace_name.lower()
                )
            )
            return result.scalars().first()

    async def get_user_by_active_workspace(
        self, keycloak_user_id: str
    ) -> Optional[JiraUser]:
        """Get Jira user by Keycloak user ID."""
        async with a_session_maker() as session:
            result = await session.execute(
                select(JiraUser).filter(
                    and_(
                        JiraUser.keycloak_user_id == keycloak_user_id,
                        JiraUser.status == 'active',
                    )
                )
            )
            return result.scalars().first()

    async def get_user_by_keycloak_id_and_workspace(
        self, keycloak_user_id: str, jira_workspace_id: int
    ) -> Optional[JiraUser]:
        """Get Jira user by Keycloak user ID and workspace ID."""
        async with a_session_maker() as session:
            result = await session.execute(
                select(JiraUser).filter(
                    and_(
                        JiraUser.keycloak_user_id == keycloak_user_id,
                        JiraUser.jira_workspace_id == jira_workspace_id,
                    )
                )
            )
            return result.scalars().first()

    async def get_active_user(
        self, jira_user_id: str, jira_workspace_id: int
    ) -> Optional[JiraUser]:
        """Get Jira user by Keycloak user ID and workspace ID."""
        async with a_session_maker() as session:
            result = await session.execute(
                select(JiraUser).filter(
                    and_(
                        JiraUser.jira_user_id == jira_user_id,
                        JiraUser.jira_workspace_id == jira_workspace_id,
                        JiraUser.status == 'active',
                    )
                )
            )
            return result.scalars().first()

    async def get_active_user_by_keycloak_id_and_workspace(
        self, keycloak_user_id: str, jira_workspace_id: int
    ) -> Optional[JiraUser]:
        """Get active Jira user by Keycloak user ID and workspace ID."""
        async with a_session_maker() as session:
            result = await session.execute(
                select(JiraUser).filter(
                    and_(
                        JiraUser.keycloak_user_id == keycloak_user_id,
                        JiraUser.jira_workspace_id == jira_workspace_id,
                        JiraUser.status == 'active',
                    )
                )
            )
            return result.scalars().first()

    async def get_or_create_active_email_link(
        self, keycloak_user_id: str, jira_workspace_id: int
    ) -> Optional[JiraUser]:
        """Return the user's active link for this workspace, reactivating a prior
        inactive link or creating one if none exists.

        Used by email-match mode to auto-enroll a matched user; a new row mirrors a
        manually-linked email row ('unavailable' Jira id, no OAuth identity). Reuses
        the existing (user, workspace) row so callers that treat the pair as unique
        stay valid. Safe under concurrent first webhooks: a losing write trips the
        partial unique index and we re-read the winner.
        """
        async with a_session_maker() as session:
            # Match ANY status so a prior inactive link is reactivated in place,
            # not duplicated.
            result = await session.execute(
                select(JiraUser).filter(
                    and_(
                        JiraUser.keycloak_user_id == keycloak_user_id,
                        JiraUser.jira_workspace_id == jira_workspace_id,
                    )
                )
            )
            user = result.scalars().first()
            if user is not None and user.status == 'active':
                return user

            if user is None:
                user = JiraUser(
                    keycloak_user_id=keycloak_user_id,
                    jira_user_id='unavailable',
                    jira_workspace_id=jira_workspace_id,
                    status='active',
                )
                session.add(user)
            else:
                user.status = 'active'

            try:
                await session.commit()
                await session.refresh(user)
                logger.info(
                    f'[Jira] Auto-enrolled email-match user for workspace '
                    f'{jira_workspace_id}'
                )
                return user
            except IntegrityError:
                await session.rollback()

        # A concurrent insert, or an active link in another workspace (the
        # one-active-link index), won; return whatever active row now exists.
        active = await self.get_active_user_by_keycloak_id_and_workspace(
            keycloak_user_id, jira_workspace_id
        )
        if active is None:
            # Most likely cause for an empty re-read: the user already holds an
            # active link in a different workspace (one-active-link constraint), so
            # auto-enroll here is rejected and they'll see "account not linked".
            logger.warning(
                f'[Jira] Could not auto-enroll {keycloak_user_id} in workspace '
                f'{jira_workspace_id}; they likely have an active link in another '
                f'workspace (one-active-link constraint)'
            )
        return active

    async def get_active_workspace_for_org(
        self, effective_org_id: UUID | None
    ) -> Optional[JiraWorkspace]:
        """Get the first active workspace visible to the given org, if any.

        Jira Cloud is multi-workspace install-wide, so this filters the active
        workspaces through the org-visibility predicate rather than assuming a
        single row. Used by the status endpoint to give members guidance.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(JiraWorkspace)
                .filter(JiraWorkspace.status == 'active')
                .order_by(JiraWorkspace.id)
            )
            workspaces = result.scalars().all()

        for workspace in workspaces:
            if workspace_visible_to_org(workspace, effective_org_id):
                return workspace
        return None

    async def update_user_integration_status(
        self, keycloak_user_id: str, status: str
    ) -> JiraUser:
        """Update Jira user integration status."""
        async with a_session_maker() as session:
            result = await session.execute(
                select(JiraUser).filter(JiraUser.keycloak_user_id == keycloak_user_id)
            )
            jira_user = result.scalars().first()

            if not jira_user:
                raise ValueError(
                    f'Jira user not found for Keycloak ID: {keycloak_user_id}'
                )

            jira_user.status = status
            await session.commit()
            await session.refresh(jira_user)

            logger.info(f'[Jira] Updated user {keycloak_user_id} status to {status}')
            return jira_user

    async def deactivate_workspace(self, workspace_id: int):
        """Deactivate the workspace and all user links for a given workspace."""
        async with a_session_maker() as session:
            result = await session.execute(
                select(JiraUser).filter(
                    and_(
                        JiraUser.jira_workspace_id == workspace_id,
                        JiraUser.status == 'active',
                    )
                )
            )
            users = result.scalars().all()

            for user in users:
                user.status = 'inactive'
                session.add(user)

            result = await session.execute(
                select(JiraWorkspace).filter(JiraWorkspace.id == workspace_id)
            )
            workspace = result.scalars().first()
            if workspace:
                workspace.status = 'inactive'
                session.add(workspace)

            await session.commit()

        logger.info(f'[Jira] Deactivated all user links for workspace {workspace_id}')

    async def create_conversation(self, jira_conversation: JiraConversation) -> None:
        """Create a new Jira conversation record."""
        async with a_session_maker() as session:
            session.add(jira_conversation)
            await session.commit()

    async def get_user_conversations_by_issue_id(
        self, issue_id: str, jira_user_id: int
    ) -> JiraConversation | None:
        """Get a Jira conversation by issue ID and jira user ID."""
        async with a_session_maker() as session:
            result = await session.execute(
                select(JiraConversation).filter(
                    and_(
                        JiraConversation.issue_id == issue_id,
                        JiraConversation.jira_user_id == jira_user_id,
                    )
                )
            )
            return result.scalars().first()

    @classmethod
    def get_instance(cls) -> JiraIntegrationStore:
        """Get an instance of the JiraIntegrationStore."""
        return JiraIntegrationStore()
