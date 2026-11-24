import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";

export const useDeleteOrganization = () => {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { organizationId, setOrganizationId } = useSelectedOrganizationId();

  return useMutation({
    mutationFn: () => {
      if (!organizationId) throw new Error("Organization ID is required");
      return organizationService.deleteOrganization({ orgId: organizationId });
    },
    onSuccess: () => {
      // Remove (not invalidate) stale cache before clearing the selected org, so
      // useAutoSelectOrganization can't reuse the old currentOrgId during the
      // re-render triggered by setOrganizationId(null).
      queryClient.removeQueries({
        queryKey: ["organizations"],
        exact: true,
      });
      queryClient.removeQueries({
        queryKey: ["organizations", organizationId],
      });

      // useAutoSelectOrganization waits for fresh data since the cache is empty
      setOrganizationId(null);

      navigate("/");
    },
  });
};
