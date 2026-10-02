import { useMutation } from "@tanstack/react-query";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";

export const useIssuePasswordReset = () => {
  const { organizationId } = useSelectedOrganizationId();

  return useMutation({
    mutationFn: ({ userId }: { userId: string }) => {
      if (!organizationId) {
        throw new Error("Organization ID is required");
      }
      return organizationService.issuePasswordReset({
        orgId: organizationId,
        userId,
      });
    },
  });
};

export const useReissuePasswordSetupLink = () => {
  const { organizationId } = useSelectedOrganizationId();

  return useMutation({
    mutationFn: ({ invitationId }: { invitationId: number }) => {
      if (!organizationId) {
        throw new Error("Organization ID is required");
      }
      return organizationService.reissuePasswordSetupLink({
        orgId: organizationId,
        invitationId,
      });
    },
  });
};
