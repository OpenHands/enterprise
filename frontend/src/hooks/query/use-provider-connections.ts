import { useQuery } from "@tanstack/react-query";
import OrgProviderConnectionsService from "#/api/organization-service/org-provider-connections-service.api";
import { useIsAuthed } from "./use-is-authed";
import {
  CONFIG_CACHE_OPTIONS,
  PROVIDER_CONNECTIONS_QUERY_KEYS,
} from "./query-keys";

/**
 * Provider connections are org-scoped (the
 * `/api/organizations/{orgId}/provider-connections` CRUD routes). The query is
 * disabled until an org is bound; there it returns no data so the connections
 * UI hides itself rather than firing an unaddressable request.
 *
 * The org list route wraps results as `{ connections: [...] }`; the service
 * unwraps that so callers see a flat `ProviderConnection[]`.
 */
export function useProviderConnections(orgId: string | null | undefined) {
  const { data: userIsAuthenticated } = useIsAuthed();

  return useQuery({
    queryKey: [...PROVIDER_CONNECTIONS_QUERY_KEYS.all, orgId],
    queryFn: async () => {
      const { connections } = await OrgProviderConnectionsService.list(orgId!);
      return connections;
    },
    enabled: !!userIsAuthenticated && !!orgId,
    ...CONFIG_CACHE_OPTIONS,
    meta: { disableToast: true },
  });
}
