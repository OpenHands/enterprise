import { useQuery } from "@tanstack/react-query";
import { licenseService } from "#/api/license-service/license-service.api";
import { useConfig } from "#/hooks/query/use-config";
import { LICENSE_QUERY_KEYS } from "#/hooks/query/use-license-status";

/** How much of each licensed limit the install uses. Only the SaaS server has it. */
export const useLicenseUsage = () => {
  const { data: config } = useConfig();
  return useQuery({
    queryKey: LICENSE_QUERY_KEYS.usage,
    queryFn: licenseService.getUsage,
    enabled: config?.app_mode === "saas",
  });
};
