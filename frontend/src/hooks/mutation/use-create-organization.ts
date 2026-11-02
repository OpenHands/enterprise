import { useMutation, useQueryClient } from "@tanstack/react-query";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { CreateOrganizationRequest } from "#/types/org";
import { SUPER_ADMIN_QUERY_KEYS } from "#/hooks/query/use-super-admin";
import { useMe } from "#/hooks/query/use-me";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { setSelectedOrg } from "#/utils/local-storage";

export const useCreateOrganization = () => {
  const queryClient = useQueryClient();
  const { data: me } = useMe();

  return useMutation({
    mutationFn: (payload: CreateOrganizationRequest) =>
      organizationService.createOrganization(payload),
    onSuccess: async (org, payload) => {
      // An organization owned by someone else does not include the caller,
      // so stay in the current organization.
      const isOwnedByOtherUser =
        !!payload.owner_user_id && payload.owner_user_id !== me?.user_id;

      if (!isOwnedByOtherUser) {
        // Select the new org so org-scoped setup (LLM defaults, members, etc.)
        // is available immediately after create.
        try {
          await organizationService.switchOrganization({ orgId: org.id });
          useSelectedOrganizationStore.getState().setOrganizationId(org.id);
          setSelectedOrg(org.id);
        } catch {
          // The server refuses a switch into an organization the caller is not
          // a member of, so stay in the current organization.
        }
      }

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
