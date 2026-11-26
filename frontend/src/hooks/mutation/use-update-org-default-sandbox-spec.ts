import { useMutation, useQueryClient } from "@tanstack/react-query";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";

export const useUpdateOrgDefaultSandboxSpec = () => {
  const queryClient = useQueryClient();
  const { organizationId } = useSelectedOrganizationId();

  return useMutation({
    mutationFn: (specName: string | null) => {
      if (!organizationId) throw new Error("Organization ID is required");
      return organizationService.updateOrganization({
        orgId: organizationId,
        default_sandbox_spec_name: specName ?? "",
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["organizations"] });
    },
  });
};
