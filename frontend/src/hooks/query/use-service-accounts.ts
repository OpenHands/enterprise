import { useQuery } from "@tanstack/react-query";
import { serviceAccountsService } from "#/api/organization-service/service-accounts.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";

export const SERVICE_ACCOUNTS_QUERY_KEY = "service-accounts";

/** Service accounts of the selected organization. */
export const useServiceAccounts = () => {
  const { organizationId } = useSelectedOrganizationId();

  return useQuery({
    queryKey: [SERVICE_ACCOUNTS_QUERY_KEY, organizationId],
    queryFn: () => serviceAccountsService.list(organizationId!),
    enabled: !!organizationId,
  });
};
