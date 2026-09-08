"""Service for managing organization invitations."""

import asyncio
from uuid import UUID

from server.auth.authorization import (
    Permission,
    get_user_super_role,
    has_permission,
)
from server.auth.token_manager import TokenManager
from server.constants import ROLE_ADMIN, ROLE_OWNER
from server.routes.org_invitation_models import (
    EmailMismatchError,
    InsufficientPermissionError,
    InvitationExpiredError,
    InvitationInvalidError,
    UserAlreadyMemberError,
)
from server.services.smtp_email_service import SMTPEmailService
from storage.org_invitation import OrgInvitation
from storage.org_invitation_store import OrgInvitationStore
from storage.org_member_store import OrgMemberStore
from storage.org_service import OrgService
from storage.org_store import OrgStore
from storage.role_store import RoleStore
from storage.user import User
from storage.user_store import UserStore

from openhands.app_server.utils.logger import openhands_logger as logger


class OrgInvitationService:
    """Service for organization invitation operations."""

    @staticmethod
    async def _authorize_inviter(
        org_id: UUID,
        inviter_id: UUID,
        role_name_lower: str,
    ) -> None:
        """Authorize a caller to invite users into ``org_id``.

        Two kinds of caller are allowed:

        * An **org-scoped** ``owner``/``admin`` member of the org (the
          normal path).
        * An instance-level **super-role** holder whose super permissions
          include ``INVITE_USER_TO_ORGANIZATION`` (a ``superadmin``). This
          lets a superadmin seed a freshly-created org with its initial
          owner/admin through the normal invitation flow, without first
          becoming a member of that org. See OHE-2769.

        The owner-role escalation guard is preserved for org-scoped
        inviters (only an ``owner`` may invite another ``owner``) but does
        not apply to a super-role inviter, since seeding the first owner is
        the whole point of the superadmin path.

        Raises:
            InsufficientPermissionError: If the caller may not invite, or
                may not invite with the requested role.
        """
        inviter_member = await OrgMemberStore.get_org_member(org_id, inviter_id)
        inviter_role = (
            await RoleStore.get_role_by_id(inviter_member.role_id)
            if inviter_member
            else None
        )
        is_org_inviter = bool(
            inviter_role and inviter_role.name in [ROLE_OWNER, ROLE_ADMIN]
        )

        # Only consult the (cross-org) super role when the org-scoped check
        # did not already grant access — keeps the common member path free
        # of an extra lookup.
        is_super_inviter = False
        if not is_org_inviter:
            super_role = await get_user_super_role(str(inviter_id))
            is_super_inviter = bool(
                super_role
                and has_permission(
                    super_role,
                    Permission.INVITE_USER_TO_ORGANIZATION,
                    is_super=True,
                )
            )

        if not (is_org_inviter or is_super_inviter):
            if inviter_member is None:
                raise InsufficientPermissionError(
                    'You are not a member of this organization'
                )
            raise InsufficientPermissionError('Only owners and admins can invite users')

        # Only an org owner may invite another owner. A super-role inviter
        # is exempt: seeding the initial owner is the intended use case.
        if (
            role_name_lower == ROLE_OWNER
            and not (inviter_role and inviter_role.name == ROLE_OWNER)
            and not is_super_inviter
        ):
            raise InsufficientPermissionError('Only owners can invite with owner role')

    @staticmethod
    async def create_invitation(
        org_id: UUID,
        email: str,
        role_name: str,
        inviter_id: UUID,
    ) -> OrgInvitation:
        """Create a new organization invitation.

        This method:
        1. Validates the organization exists
        2. Validates this is not a personal workspace
        3. Checks inviter has owner/admin role
        4. Validates role assignment permissions
        5. Checks if user is already a member
        6. Creates the invitation
        7. Sends the invitation email

        Args:
            org_id: Organization UUID
            email: Invitee's email address
            role_name: Role to assign on acceptance (owner, admin, member)
            inviter_id: User ID of the person creating the invitation

        Returns:
            OrgInvitation: The created invitation

        Raises:
            ValueError: If organization or role not found
            InsufficientPermissionError: If inviter lacks permission
            UserAlreadyMemberError: If email is already a member
            InvitationAlreadyExistsError: If pending invitation exists
        """
        email = email.lower().strip()

        logger.info(
            'Creating organization invitation',
            extra={
                'org_id': str(org_id),
                'email': email,
                'role_name': role_name,
                'inviter_id': str(inviter_id),
            },
        )

        # Step 1: Validate organization exists
        org = await OrgStore.get_org_by_id(org_id)
        if not org:
            raise ValueError(f'Organization {org_id} not found')

        # Step 2: Check this is not a personal workspace
        # A personal workspace has org_id matching the user's id
        if str(org_id) == str(inviter_id):
            raise InsufficientPermissionError(
                'Cannot invite users to a personal workspace'
            )

        # Step 3 & 4: Authorize the inviter (org owner/admin member, or a
        # superadmin seeding the org) and validate the requested role.
        role_name_lower = role_name.lower()
        await OrgInvitationService._authorize_inviter(
            org_id, inviter_id, role_name_lower
        )

        target_role = await RoleStore.get_role_by_name(role_name_lower)
        if not target_role:
            raise ValueError(f'Invalid role: {role_name}')

        existing_user = await UserStore.get_user_by_email(email)
        if existing_user:
            existing_member = await OrgMemberStore.get_org_member(
                org_id, existing_user.id
            )
            if existing_member:
                raise UserAlreadyMemberError(
                    'User is already a member of this organization'
                )

        invitation = await OrgInvitationStore.create_invitation(
            org_id=org_id,
            email=email,
            role_id=target_role.id,
            inviter_id=inviter_id,
        )

        try:
            inviter_user = await UserStore.get_user_by_id(str(inviter_id))
            inviter_name = 'A team member'
            if inviter_user and inviter_user.email:
                inviter_name = inviter_user.email.split('@')[0]

            await asyncio.to_thread(
                SMTPEmailService.send_invitation_email,
                to_email=email,
                org_name=org.name,
                inviter_name=inviter_name,
                role_name=target_role.name,
                invitation_token=invitation.token,
                invitation_id=invitation.id,
            )
        except Exception:
            logger.exception(
                'Failed to send invitation email',
                extra={
                    'invitation_id': invitation.id,
                    'email': email,
                },
                stack_info=True,
            )
            # Don't fail the invitation creation if email fails
            # The user can still access via direct link

        return invitation

    @staticmethod
    async def create_invitations_batch(
        org_id: UUID,
        emails: list[str],
        role_name: str,
        inviter_id: UUID,
    ) -> tuple[list[OrgInvitation], list[tuple[str, str]]]:
        """Create multiple organization invitations concurrently.

        Validates permissions once upfront, then creates invitations in parallel.

        Args:
            org_id: Organization UUID
            emails: List of invitee email addresses
            role_name: Role to assign on acceptance (owner, admin, member)
            inviter_id: User ID of the person creating the invitations

        Returns:
            Tuple of (successful_invitations, failed_emails_with_errors)

        Raises:
            ValueError: If organization or role not found
            InsufficientPermissionError: If inviter lacks permission
        """
        logger.info(
            'Creating batch organization invitations',
            extra={
                'org_id': str(org_id),
                'email_count': len(emails),
                'role_name': role_name,
                'inviter_id': str(inviter_id),
            },
        )

        # Step 1: Validate permissions upfront (shared for all emails)
        org = await OrgStore.get_org_by_id(org_id)
        if not org:
            raise ValueError(f'Organization {org_id} not found')

        if str(org_id) == str(inviter_id):
            raise InsufficientPermissionError(
                'Cannot invite users to a personal workspace'
            )

        # Authorize the inviter (org owner/admin member, or a superadmin
        # seeding the org) and validate the requested role.
        role_name_lower = role_name.lower()
        await OrgInvitationService._authorize_inviter(
            org_id, inviter_id, role_name_lower
        )

        target_role = await RoleStore.get_role_by_name(role_name_lower)
        if not target_role:
            raise ValueError(f'Invalid role: {role_name}')

        # Step 2: Create invitations concurrently
        async def create_single(
            email: str,
        ) -> tuple[str, OrgInvitation | None, str | None]:
            """Create single invitation, return (email, invitation, error)."""
            try:
                invitation = await OrgInvitationService.create_invitation(
                    org_id=org_id,
                    email=email,
                    role_name=role_name,
                    inviter_id=inviter_id,
                )
                return (email, invitation, None)
            except (UserAlreadyMemberError, ValueError) as e:
                return (email, None, str(e))

        results = await asyncio.gather(*[create_single(email) for email in emails])

        # Step 3: Separate successes and failures
        successful: list[OrgInvitation] = []
        failed: list[tuple[str, str]] = []
        for email, invitation, error in results:
            if invitation:
                successful.append(invitation)
            elif error:
                failed.append((email, error))

        logger.info(
            'Batch invitation creation completed',
            extra={
                'org_id': str(org_id),
                'successful': len(successful),
                'failed': len(failed),
            },
        )

        return successful, failed

    @staticmethod
    async def accept_pending_invitations_for_user(user: User) -> list[OrgInvitation]:
        """Accept pending invitations matching the user's email at sign-in.

        SSO-native acceptance: the IdP verified the address an invitation was
        sent to, which is the same assurance as clicking the emailed token
        link — so the token never needs delivering. Also marks invitations
        accepted when the user is already a member (e.g. default-org auto-add
        joined them first), so they stop showing as pending.

        Returns:
            The invitations this call newly accepted with a membership.
        """
        user_email = (user.email or '').strip().lower()
        if not user_email:
            return []

        invitations = await OrgInvitationStore.get_pending_invitations_for_email(
            user_email
        )
        accepted: list[OrgInvitation] = []
        for invitation in invitations:
            if OrgInvitationStore.is_token_expired(invitation):
                await OrgInvitationStore.update_invitation_status(
                    invitation.id, OrgInvitation.STATUS_EXPIRED
                )
                continue

            existing_member = await OrgMemberStore.get_org_member(
                invitation.org_id, user.id
            )
            if existing_member:
                await OrgInvitationStore.update_invitation_status(
                    invitation.id,
                    OrgInvitation.STATUS_ACCEPTED,
                    accepted_by_user_id=user.id,
                )
                continue

            org = await OrgStore.get_org_by_id(invitation.org_id)
            if not org:
                continue

            try:
                settings = await OrgService.create_litellm_integration(
                    invitation.org_id, str(user.id)
                )
            except Exception:
                logger.exception(
                    'Failed to create LiteLLM integration for login-time '
                    'invitation acceptance',
                    extra={
                        'invitation_id': invitation.id,
                        'user_id': str(user.id),
                        'org_id': str(invitation.org_id),
                    },
                    stack_info=True,
                )
                continue

            llm_api_key_secret = settings.agent_settings.llm.api_key
            llm_api_key = (
                llm_api_key_secret.get_secret_value() if llm_api_key_secret else ''  # type: ignore[union-attr]
            )
            # Status flips LAST: any failure leaves the invitation pending so
            # the next sign-in retries it (the already-member branch above
            # reconciles a member whose status update was lost).
            await OrgMemberStore.add_user_to_org(
                org_id=invitation.org_id,
                user_id=user.id,
                role_id=invitation.role_id,
                llm_api_key=llm_api_key,
                status='active',
                agent_settings_diff={},
                conversation_settings_diff={},
            )
            await OrgInvitationStore.update_invitation_status(
                invitation.id,
                OrgInvitation.STATUS_ACCEPTED,
                accepted_by_user_id=user.id,
            )
            accepted.append(invitation)
            logger.info(
                'Organization invitation accepted via email match at login',
                extra={
                    'invitation_id': invitation.id,
                    'user_id': str(user.id),
                    'org_id': str(invitation.org_id),
                },
            )

        # Land the user in the first newly joined org when they're parked on
        # their personal workspace; a deliberately chosen team org is kept.
        if accepted and user.current_org_id == user.id:
            await UserStore.update_current_org(str(user.id), accepted[0].org_id)

        return accepted

    @staticmethod
    async def accept_invitation(token: str, user_id: UUID) -> OrgInvitation:
        """Accept an organization invitation.

        This method:
        1. Validates the token and invitation status
        2. Checks expiration
        3. Verifies user is not already a member
        4. Creates LiteLLM integration
        5. Adds user to the organization
        6. Marks invitation as accepted

        Args:
            token: The invitation token
            user_id: The user accepting the invitation

        Returns:
            OrgInvitation: The accepted invitation

        Raises:
            InvitationInvalidError: If token is invalid or invitation not pending
            InvitationExpiredError: If invitation has expired
            UserAlreadyMemberError: If user is already a member
        """
        logger.info(
            'Accepting organization invitation',
            extra={
                'token_prefix': token[:10] + '...' if len(token) > 10 else token,
                'user_id': str(user_id),
            },
        )

        # Step 1: Get and validate invitation
        invitation = await OrgInvitationStore.get_invitation_by_token(token)

        if not invitation:
            raise InvitationInvalidError('Invalid invitation token')

        if invitation.status != OrgInvitation.STATUS_PENDING:
            if invitation.status == OrgInvitation.STATUS_ACCEPTED:
                # The invitation may have been accepted on this user's behalf
                # already (e.g. by email match during the sign-in that just
                # happened). For the invited user that is success, not an
                # invalid token.
                existing_member = await OrgMemberStore.get_org_member(
                    invitation.org_id, user_id
                )
                if existing_member:
                    raise UserAlreadyMemberError(
                        'You are already a member of this organization'
                    )
                raise InvitationInvalidError('Invitation has already been accepted')
            elif invitation.status == OrgInvitation.STATUS_REVOKED:
                raise InvitationInvalidError('Invitation has been revoked')
            else:
                raise InvitationInvalidError('Invitation is no longer valid')

        # Step 2: Check expiration
        if OrgInvitationStore.is_token_expired(invitation):
            await OrgInvitationStore.update_invitation_status(
                invitation.id, OrgInvitation.STATUS_EXPIRED
            )
            raise InvitationExpiredError('Invitation has expired')

        # Step 2.5: Verify user email matches invitation email
        user = await UserStore.get_user_by_id(str(user_id))
        if not user:
            raise InvitationInvalidError('User not found')

        user_email = user.email
        # Fallback: fetch email from Keycloak if not in database (for existing users).
        # When found, persist it back to User.email so the members list shows it
        # without requiring the user to log out and log back in.
        if not user_email:
            token_manager = TokenManager()
            user_info = await token_manager.get_user_info_from_user_id(str(user_id))
            if user_info:
                user_email = user_info.get('email')
                if user_email:
                    await UserStore.backfill_user_email(
                        str(user_id),
                        {
                            'email': user_email,
                            'email_verified': user_info.get('emailVerified', False),
                        },
                    )

        if not user_email:
            raise EmailMismatchError('Your account does not have an email address')

        user_email = user_email.lower().strip()
        invitation_email = invitation.email.lower().strip()

        if user_email != invitation_email:
            logger.warning(
                'Email mismatch during invitation acceptance',
                extra={
                    'user_id': str(user_id),
                    'user_email': user_email,
                    'invitation_email': invitation_email,
                    'invitation_id': invitation.id,
                },
            )
            raise EmailMismatchError()

        # Step 3: Check if user is already a member
        existing_member = await OrgMemberStore.get_org_member(
            invitation.org_id, user_id
        )
        if existing_member:
            raise UserAlreadyMemberError(
                'You are already a member of this organization'
            )

        # Step 4: Create LiteLLM integration for the user in the new org
        try:
            settings = await OrgService.create_litellm_integration(
                invitation.org_id, str(user_id)
            )
        except Exception as e:
            logger.exception(
                'Failed to create LiteLLM integration for invitation acceptance',
                extra={
                    'invitation_id': invitation.id,
                    'user_id': str(user_id),
                    'org_id': str(invitation.org_id),
                },
                stack_info=True,
            )
            raise InvitationInvalidError(
                'Failed to set up organization access. Please try again.'
            ) from e

        # Step 4.5: Ensure the organization still exists before adding membership
        org = await OrgStore.get_org_by_id(invitation.org_id)
        if not org:
            raise InvitationInvalidError('Organization not found')

        # Step 5: Add user to organization. New members start with no
        # personal agent-setting overrides so future org default changes
        # continue to flow through automatically.
        llm_api_key_secret = settings.agent_settings.llm.api_key
        llm_api_key = (
            llm_api_key_secret.get_secret_value() if llm_api_key_secret else ''  # type: ignore[union-attr]
        )

        await OrgMemberStore.add_user_to_org(
            org_id=invitation.org_id,
            user_id=user_id,
            role_id=invitation.role_id,
            llm_api_key=llm_api_key,
            status='active',
            agent_settings_diff={},
            conversation_settings_diff={},
        )

        # Step 6: Mark invitation as accepted
        updated_invitation = await OrgInvitationStore.update_invitation_status(
            invitation.id,
            OrgInvitation.STATUS_ACCEPTED,
            accepted_by_user_id=user_id,
        )

        if not updated_invitation:
            raise InvitationInvalidError('Failed to update invitation status')

        logger.info(
            'Organization invitation accepted',
            extra={
                'invitation_id': invitation.id,
                'user_id': str(user_id),
                'org_id': str(invitation.org_id),
                'role_id': invitation.role_id,
            },
        )

        return updated_invitation

    @staticmethod
    async def revoke_invitation(
        org_id: UUID, invitation_id: int
    ) -> OrgInvitation | None:
        """Revoke a pending invitation, invalidating its token/link.

        Returns None when the invitation doesn't exist or belongs to a
        different org (the caller's org_id comes from the URL path, so a
        mismatch must look identical to not-found).

        Raises:
            InvitationInvalidError: If the invitation is not pending
        """
        invitation = await OrgInvitationStore.get_invitation_by_id(invitation_id)
        if not invitation or invitation.org_id != org_id:
            return None

        if invitation.status != OrgInvitation.STATUS_PENDING:
            raise InvitationInvalidError('Only pending invitations can be revoked')

        revoked = await OrgInvitationStore.update_invitation_status(
            invitation_id, OrgInvitation.STATUS_REVOKED
        )
        logger.info(
            'Organization invitation revoked',
            extra={'invitation_id': invitation_id, 'org_id': str(org_id)},
        )
        return revoked
