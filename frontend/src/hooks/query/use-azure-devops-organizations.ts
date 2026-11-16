import { useQuery } from "@tanstack/react-query";
import { integrationService } from "#/api/integration-service/integration-service.api";

export function useAzureDevOpsOrganizations() {
  return useQuery({
    queryKey: ["azure-devops-organizations"],
    queryFn: () => integrationService.getAzureDevOpsOrganizations(),
    staleTime: 1000 * 60 * 2,
    gcTime: 1000 * 60 * 10,
  });
}
