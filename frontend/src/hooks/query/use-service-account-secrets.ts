import { useQuery } from "@tanstack/react-query";
import { serviceAccountsService } from "#/api/organization-service/service-accounts.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { SERVICE_ACCOUNTS_QUERY_KEY } from "./use-service-accounts";

/** Names and descriptions of one service account's secrets. Values are never returned. */
export const useServiceAccountSecrets = (saId: string) => {
  const { organizationId } = useSelectedOrganizationId();

  return useQuery({
    queryKey: [SERVICE_ACCOUNTS_QUERY_KEY, organizationId, saId, "secrets"],
    queryFn: async () =>
      (await serviceAccountsService.listSecrets(organizationId!, saId)).items,
    enabled: !!organizationId,
  });
};
