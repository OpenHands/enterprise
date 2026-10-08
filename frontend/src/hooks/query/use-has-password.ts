import { useQuery } from "@tanstack/react-query";
import { idpService } from "#/api/idp-service/idp-service.api";
import { QUERY_KEYS } from "./query-keys";

interface UseHasPasswordOptions {
  enabled?: boolean;
}

/**
 * Whether the current user already has a dev-IDP password set. Only call
 * this when ``enable_integrated_idp`` is on — the endpoint 404s otherwise.
 */
export const useHasPassword = (options?: UseHasPasswordOptions) =>
  useQuery({
    queryKey: QUERY_KEYS.IDP_HAS_PASSWORD,
    queryFn: () => idpService.hasPassword(),
    enabled: options?.enabled ?? true,
  });
