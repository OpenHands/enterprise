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
 * disabled until an org is bound, so callers that pass `null` (personal scope,
 * or the org form while its selector is hidden) never fire the request. The
 * list itself only needs VIEW_ORG_SETTINGS, so the org profiles list fetches
 * it for every member to label profile groups.
 *
 * The org list route wraps results as `{ connections: [...] }`; this hook
 * unwraps that so callers see a flat `ProviderConnection[]`.
 */
export function useProviderConnections(orgId: string | null | undefined) {
  const { data: userIsAuthenticated } = useIsAuthed();

  return useQuery({
    queryKey: PROVIDER_CONNECTIONS_QUERY_KEYS.byOrg(orgId),
    queryFn: async () => {
      const { connections } = await OrgProviderConnectionsService.list(orgId!);
      return connections;
    },
    enabled: !!userIsAuthenticated && !!orgId,
    ...CONFIG_CACHE_OPTIONS,
    meta: { disableToast: true },
  });
}
