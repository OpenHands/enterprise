import { useMutation, useQueryClient } from "@tanstack/react-query";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";

export const useUpdateSharedConversationDiscovery = () => {
  const queryClient = useQueryClient();
  const { organizationId } = useSelectedOrganizationId();

  return useMutation({
    mutationFn: (enabled: boolean) => {
      if (!organizationId) throw new Error("Organization ID is required");
      return organizationService.updateOrganization({
        orgId: organizationId,
        enable_shared_conversation_discovery: enabled,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["organizations"],
      });
    },
  });
};
