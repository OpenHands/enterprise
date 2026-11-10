import { useQuery } from "@tanstack/react-query";
import { licenseService } from "#/api/license-service/license-service.api";
import { LICENSE_QUERY_KEYS } from "#/hooks/query/use-license-status";

/** The license this install uses. Only a Super Admin can read it. */
export const useConfiguredLicense = () =>
  useQuery({
    queryKey: LICENSE_QUERY_KEYS.configured,
    queryFn: licenseService.getLicense,
  });
