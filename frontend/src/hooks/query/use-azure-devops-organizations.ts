import { useQuery } from "@tanstack/react-query";
import { integrationService } from "#/api/integration-service/integration-service.api";

export function useAzureDevOpsOrganizations() {
  return useQuery({
    queryKey: ["azure-devops-organizations"],
    queryFn: () => integrationService.getAzureDevOpsOrganizations(),
  });
}
