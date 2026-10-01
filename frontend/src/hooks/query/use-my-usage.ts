import { useQuery } from "@tanstack/react-query";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";

export const useMyUsage = ({ timeWindow }: { timeWindow: string }) => {
  const { organizationId } = useSelectedOrganizationId();
  return useQuery({
    queryKey: ["organizations", "my-usage", organizationId, timeWindow],
    queryFn: () =>
      organizationService.getMyUsage({
        orgId: organizationId!,
        timeWindow,
      }),
    enabled: !!organizationId,
  });
};
