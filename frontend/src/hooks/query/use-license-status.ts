import { useQuery } from "@tanstack/react-query";
import { licenseService } from "#/api/license-service/license-service.api";

export const LICENSE_QUERY_KEYS = {
  status: ["license", "status"] as const,
};

export const useLicenseStatus = (options?: { enabled?: boolean }) =>
  useQuery({
    queryKey: LICENSE_QUERY_KEYS.status,
    queryFn: licenseService.getStatus,
    // The server checks the license every 30 minutes by default, so a few
    // minutes of staleness is fine.
    staleTime: 5 * 60_000,
    refetchInterval: 5 * 60_000,
    enabled: options?.enabled,
  });
