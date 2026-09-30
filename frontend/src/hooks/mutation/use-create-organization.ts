import { useMutation, useQueryClient } from "@tanstack/react-query";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { CreateOrganizationRequest } from "#/types/org";
import { SUPER_ADMIN_QUERY_KEYS } from "#/hooks/query/use-super-admin";

export const useCreateOrganization = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: CreateOrganizationRequest) =>
      organizationService.createOrganization(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["organizations"] });
      queryClient.invalidateQueries({
        queryKey: SUPER_ADMIN_QUERY_KEYS.organizations,
      });
    },
  });
};
