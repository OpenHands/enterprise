import { useQuery } from "@tanstack/react-query";
import { integrationService } from "#/api/integration-service/integration-service.api";
import type { AzureDevOpsWebhookStatus } from "#/api/integration-service/integration-service.types";

export function useAzureDevOpsResources(
  enabled: boolean = true,
  organization?: string,
) {
  return useQuery<AzureDevOpsWebhookStatus>({
    queryKey: ["azure-devops-resources", organization],
    queryFn: () => integrationService.getAzureDevOpsResources(organization),
    enabled,
    staleTime: 1000 * 60 * 2,
    gcTime: 1000 * 60 * 10,
  });
}
