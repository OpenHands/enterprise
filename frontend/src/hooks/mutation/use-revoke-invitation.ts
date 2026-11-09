import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

export const useRevokeInvitation = () => {
  const queryClient = useQueryClient();
  const { organizationId } = useSelectedOrganizationId();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({ invitationId }: { invitationId: number }) => {
      if (!organizationId) {
        throw new Error("Organization ID is required");
      }
      return organizationService.revokeInvitation({
        orgId: organizationId,
        invitationId,
      });
    },
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.ORG$INVITATION_REVOKED));
      queryClient.invalidateQueries({
        queryKey: ["organizations", "pending-invitations", organizationId],
      });
    },
    meta: { disableToast: true },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.ORG$INVITATION_REVOKE_ERROR),
      );
    },
  });
};
