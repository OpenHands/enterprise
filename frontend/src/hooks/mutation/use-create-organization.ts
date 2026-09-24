import { useMutation, useQueryClient } from "@tanstack/react-query";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { CreateOrganizationRequest } from "#/types/org";
import { SUPER_ADMIN_QUERY_KEYS } from "#/hooks/query/use-super-admin";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { setSelectedOrg } from "#/utils/local-storage";

export const useCreateOrganization = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: CreateOrganizationRequest) =>
      organizationService.createOrganization(payload),
    onSuccess: async (org) => {
      // Select the new org so org-scoped setup (LLM defaults, members, etc.)
      // is available immediately after create.
      try {
        await organizationService.switchOrganization({ orgId: org.id });
      } catch {
        // Local selection still helps even if the switch API fails.
      }
      useSelectedOrganizationStore.getState().setOrganizationId(org.id);
      setSelectedOrg(org.id);

      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["organizations"] }),
        queryClient.invalidateQueries({
          queryKey: SUPER_ADMIN_QUERY_KEYS.organizations,
        }),
        queryClient.invalidateQueries({
          queryKey: ["organizations", org.id, "me"],
        }),
      ]);
    },
  });
};
