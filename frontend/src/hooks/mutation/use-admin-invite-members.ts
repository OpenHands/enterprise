import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { OrganizationUserRole } from "#/types/org";

/**
 * Invite users into an arbitrary organization as a super admin.
 *
 * Unlike {@link useInviteMembersBatch} (which targets the caller's currently
 * selected org), the target org is passed per call so the dashboard can invite
 * into any org in the instance. The backing endpoint authorizes the super role
 * for any org, so no membership in the target org is required.
 */
export const useAdminInviteMembers = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({
      orgId,
      emails,
      role,
    }: {
      orgId: string;
      emails: string[];
      role: OrganizationUserRole;
    }) => organizationService.inviteMembers({ orgId, emails, role }),
    onSuccess: (_data, { orgId }) => {
      displaySuccessToast(t(I18nKey.ORG$INVITE_MEMBERS_SUCCESS));
      queryClient.invalidateQueries({
        queryKey: ["organizations", "pending-invitations", orgId],
      });
    },
    onError: (error) => {
      const errorMessage = retrieveAxiosErrorMessage(error);
      displayErrorToast(errorMessage || t(I18nKey.ORG$INVITE_MEMBERS_ERROR));
    },
  });
};
