import React from "react";
import ReactDOM from "react-dom";
import type { AxiosError } from "axios";
import { useTranslation } from "react-i18next";
import { LoaderCircle, Plus, Search } from "lucide-react";
import { InviteOrganizationMemberModal } from "#/components/features/org/invite-organization-member-modal";
import { ConfirmRemoveMemberModal } from "#/components/features/org/confirm-remove-member-modal";
import { ConfirmUpdateRoleModal } from "#/components/features/org/confirm-update-role-modal";
import { useOrganizationMembers } from "#/hooks/query/use-organization-members";
import { useOrganizationMembersCount } from "#/hooks/query/use-organization-members-count";
import {
  OrganizationMember,
  OrganizationUserRole,
  PasswordLinkResponse,
} from "#/types/org";
import { OrganizationMemberListItem } from "#/components/features/org/organization-member-list-item";
import { PendingInvitationListItem } from "#/components/features/org/pending-invitation-list-item";
import { PasswordLinkModal } from "#/components/features/org/password-link-modal";
import { usePendingInvitations } from "#/hooks/query/use-pending-invitations";
import { useRevokeInvitation } from "#/hooks/mutation/use-revoke-invitation";
import { useUpdateMemberRole } from "#/hooks/mutation/use-update-member-role";
import { useRemoveMember } from "#/hooks/mutation/use-remove-member";
import {
  useIssuePasswordReset,
  useReissuePasswordSetupLink,
} from "#/hooks/mutation/use-password-links";
import { useMe } from "#/hooks/query/use-me";
import { useConfig } from "#/hooks/query/use-config";
import { BrandButton } from "#/components/features/settings/brand-button";
import { rolePermissions } from "#/utils/org/permissions";
import { I18nKey } from "#/i18n/declaration";
import { usePermission } from "#/hooks/organizations/use-permissions";
import { getAvailableRolesAUserCanAssign } from "#/utils/org/permission-checks";
import { createPermissionGuard } from "#/utils/org/permission-guard";
import { Typography } from "#/ui/typography";
import { Pagination } from "#/ui/pagination";
import { useDebounce } from "#/hooks/use-debounce";
import { cn } from "#/utils/utils";
import { displayErrorToast } from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import {
  formControlInlineInputClassName,
  formControlShellClassName,
} from "#/utils/form-control-classes";
import {
  settingsListContainerClassName,
  settingsListSectionHeaderClassName,
  settingsListTableRowClassName,
} from "#/utils/settings-list-classes";

export const clientLoader = createPermissionGuard(
  "invite_user_to_organization",
);

export const handle = { hideTitle: true };

function ManageOrganizationMembers() {
  const { t } = useTranslation();

  // Pagination and filtering state
  const [page, setPage] = React.useState(1);
  const [emailFilter, setEmailFilter] = React.useState("");
  const debouncedEmailFilter = useDebounce(emailFilter, 300);

  // Reset to page 1 when filter changes
  React.useEffect(() => {
    setPage(1);
  }, [debouncedEmailFilter]);

  const limit = 10;

  const {
    data: membersData,
    isLoading,
    isFetching,
    error: membersError,
  } = useOrganizationMembers({
    page,
    limit,
    email: debouncedEmailFilter,
  });

  const { data: totalCount, error: countError } = useOrganizationMembersCount({
    email: debouncedEmailFilter,
  });

  const hasError = membersError || countError;

  const { data: user } = useMe();
  const { data: config } = useConfig();
  const { mutate: updateMemberRole, isPending: isUpdatingRole } =
    useUpdateMemberRole();
  const { mutate: removeMember, isPending: isRemovingMember } =
    useRemoveMember();
  const issuePasswordReset = useIssuePasswordReset();
  const reissuePasswordSetupLink = useReissuePasswordSetupLink();
  const [passwordLink, setPasswordLink] = React.useState<{
    link: PasswordLinkResponse;
    email: string;
  } | null>(null);
  const [inviteModalOpen, setInviteModalOpen] = React.useState(false);
  const [memberToRemove, setMemberToRemove] =
    React.useState<OrganizationMember | null>(null);
  const [memberToUpdateRole, setMemberToUpdateRole] = React.useState<{
    member: OrganizationMember;
    newRole: OrganizationUserRole;
  } | null>(null);

  const currentUserRole = user?.role ?? "member";

  const { hasPermission } = usePermission(currentUserRole);
  const hasPermissionToInvite = hasPermission("invite_user_to_organization");

  // Pending invitations render as rows in the members list (with an
  // "Invited" chip); the backing endpoint is invite-permission gated.
  const { data: pendingData } = usePendingInvitations(hasPermissionToInvite);
  const { mutate: revokeInvitation, isPending: isRevokingInvitation } =
    useRevokeInvitation();
  const pendingInvitations = (pendingData?.items ?? []).filter(
    (invitation) =>
      !debouncedEmailFilter ||
      invitation.email
        .toLowerCase()
        .includes(debouncedEmailFilter.toLowerCase()),
  );

  // Calculate total pages
  const totalPages =
    totalCount !== undefined ? Math.ceil(totalCount / limit) : 0;

  const handleRoleSelectionClick = (
    member: OrganizationMember,
    role: OrganizationUserRole,
  ) => {
    // Don't show modal if the role is the same
    if (member.role === role) {
      return;
    }
    setMemberToUpdateRole({ member, newRole: role });
  };

  const handleConfirmUpdateRole = () => {
    if (memberToUpdateRole) {
      updateMemberRole(
        {
          userId: memberToUpdateRole.member.user_id,
          role: memberToUpdateRole.newRole,
        },
        { onSettled: () => setMemberToUpdateRole(null) },
      );
    }
  };

  const handleRemoveMember = (member: OrganizationMember) => {
    setMemberToRemove(member);
  };

  const handleConfirmRemoveMember = () => {
    if (memberToRemove) {
      removeMember(
        { userId: memberToRemove.user_id },
        { onSettled: () => setMemberToRemove(null) },
      );
    }
  };

  const availableRolesToChangeTo = getAvailableRolesAUserCanAssign(
    rolePermissions[currentUserRole],
  );

  const isCurrentUserSuperadmin =
    user?.permissions?.includes("manage_super_admins") ?? false;
  const canManageMember = (member: OrganizationMember) =>
    user != null &&
    user.user_id !== member.user_id &&
    (!member.is_superadmin || isCurrentUserSuperadmin);
  const canAssignUserRole = (member: OrganizationMember) =>
    canManageMember(member) && hasPermission(`change_user_role:${member.role}`);
  const canRemoveMember = (member: OrganizationMember) =>
    canAssignUserRole(member) ||
    (isCurrentUserSuperadmin && canManageMember(member));

  const handlePasswordReset = (member: OrganizationMember) => {
    issuePasswordReset.mutate(
      { userId: member.user_id },
      {
        onSuccess: (link) =>
          setPasswordLink({ link, email: member.email ?? "" }),
        onError: (error) =>
          displayErrorToast(
            retrieveAxiosErrorMessage(error) ||
              t(I18nKey.ORG$PASSWORD_LINK_ERROR),
          ),
      },
    );
  };

  const requestSetupLink = async (invitationId: number) => {
    try {
      const link = await reissuePasswordSetupLink.mutateAsync({ invitationId });
      return link.url;
    } catch (error) {
      displayErrorToast(
        retrieveAxiosErrorMessage(error as AxiosError) ||
          t(I18nKey.ORG$PASSWORD_LINK_ERROR),
      );
      return "";
    }
  };

  return (
    <div
      data-testid="manage-organization-members-settings"
      className="flex h-full flex-col gap-6"
    >
      <div className="flex items-start justify-between gap-4">
        <header className="min-w-0 space-y-1">
          <Typography.H2>{t(I18nKey.ORG$ORGANIZATION_MEMBERS)}</Typography.H2>
          <p
            data-testid="settings-page-subtitle"
            className="text-sm leading-5 text-muted"
          >
            {t(I18nKey.SETTINGS$PAGE_ORG_MEMBERS_SUBLINE)}
          </p>
        </header>
        {hasPermissionToInvite && (
          <BrandButton
            type="button"
            variant="primary"
            className="shrink-0 whitespace-nowrap"
            onClick={() => setInviteModalOpen(true)}
            startContent={<Plus size={14} />}
          >
            {t(I18nKey.ORG$INVITE_ORG_MEMBERS)}
          </BrandButton>
        )}
      </div>

      {/* Email Search Input */}
      <div className={cn(formControlShellClassName, "w-full")}>
        <Search
          size={16}
          className="ml-3 shrink-0 text-tertiary-alt"
          aria-hidden
        />
        <input
          data-testid="email-filter-input"
          type="text"
          value={emailFilter}
          placeholder={t(I18nKey.ORG$SEARCH_BY_EMAIL)}
          onChange={(e) => setEmailFilter(e.target.value)}
          className={cn(formControlInlineInputClassName, "text-white")}
        />
        {isFetching && debouncedEmailFilter && (
          <LoaderCircle
            size={16}
            className="mr-3 shrink-0 animate-spin text-tertiary-alt"
            data-testid="search-loading-indicator"
          />
        )}
      </div>

      {inviteModalOpen &&
        ReactDOM.createPortal(
          <InviteOrganizationMemberModal
            onClose={() => setInviteModalOpen(false)}
          />,
          document.getElementById("portal-root") || document.body,
        )}

      <div
        className={cn(
          settingsListContainerClassName,
          "custom-scrollbar flex-1 overflow-y-auto",
        )}
      >
        <div className={settingsListSectionHeaderClassName}>
          <span>{t(I18nKey.ORG$ALL_ORGANIZATION_MEMBERS)}</span>
          {totalCount !== undefined && (
            <span className="text-muted">
              {totalCount} {totalCount === 1 ? "member" : "members"}
            </span>
          )}
        </div>

        {isLoading && (
          <div className="flex items-center justify-center p-8 text-muted">
            Loading...
          </div>
        )}

        {!isLoading && hasError && (
          <div className="flex items-center justify-center p-8 text-muted">
            {t(I18nKey.ORG$FAILED_TO_LOAD_MEMBERS)}
          </div>
        )}

        {!isLoading &&
          !hasError &&
          ((membersData?.items && membersData.items.length > 0) ||
            pendingInvitations.length > 0) && (
            <ul data-testid="organization-members-list">
              {membersData?.items?.map((member) => (
                <li
                  key={member.user_id}
                  data-testid="member-item"
                  className={settingsListTableRowClassName}
                >
                  <OrganizationMemberListItem
                    email={member.email}
                    role={member.role}
                    status={member.status}
                    isSuperadmin={member.is_superadmin}
                    hasPassword={member.has_password}
                    showPasswordState={config?.password_auth_enabled}
                    hasPermissionToChangeRole={canAssignUserRole(member)}
                    availableRolesToChangeTo={availableRolesToChangeTo}
                    onRoleChange={(role) =>
                      handleRoleSelectionClick(member, role)
                    }
                    onRemove={
                      canRemoveMember(member)
                        ? () => handleRemoveMember(member)
                        : undefined
                    }
                    onResetPassword={
                      config?.password_auth_enabled && canManageMember(member)
                        ? () => handlePasswordReset(member)
                        : undefined
                    }
                  />
                </li>
              ))}
              {pendingInvitations.map((invitation) => (
                <li
                  key={`invitation-${invitation.id}`}
                  className={settingsListTableRowClassName}
                >
                  <PendingInvitationListItem
                    invitation={invitation}
                    isRevoking={isRevokingInvitation}
                    onRevoke={() =>
                      revokeInvitation({ invitationId: invitation.id })
                    }
                    onRequestInviteUrl={
                      config?.password_auth_enabled
                        ? () => requestSetupLink(invitation.id)
                        : undefined
                    }
                  />
                </li>
              ))}
            </ul>
          )}

        {!isLoading &&
          !hasError &&
          pendingInvitations.length === 0 &&
          (!membersData?.items || membersData.items.length === 0) && (
            <div className="flex items-center justify-center p-8 text-muted">
              {debouncedEmailFilter
                ? t(I18nKey.ORG$NO_MEMBERS_MATCHING_FILTER)
                : t(I18nKey.ORG$NO_MEMBERS_FOUND)}
            </div>
          )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <Pagination
          currentPage={page}
          totalPages={totalPages}
          onPageChange={setPage}
          className="py-4"
        />
      )}

      {memberToRemove && (
        <ConfirmRemoveMemberModal
          memberEmail={memberToRemove.email}
          onConfirm={handleConfirmRemoveMember}
          onCancel={() => setMemberToRemove(null)}
          isLoading={isRemovingMember}
        />
      )}

      {memberToUpdateRole && (
        <ConfirmUpdateRoleModal
          memberEmail={memberToUpdateRole.member.email}
          newRole={memberToUpdateRole.newRole}
          onConfirm={handleConfirmUpdateRole}
          onCancel={() => setMemberToUpdateRole(null)}
          isLoading={isUpdatingRole}
        />
      )}

      {passwordLink && (
        <PasswordLinkModal
          link={passwordLink.link}
          email={passwordLink.email}
          onClose={() => setPasswordLink(null)}
        />
      )}
    </div>
  );
}

export default ManageOrganizationMembers;
