import { useQuery } from "@tanstack/react-query";
import OrgModelRoutersService from "#/api/organization-service/org-model-routers-service.api";
import { useIsAuthed } from "./use-is-authed";
import { CONFIG_CACHE_OPTIONS, MODEL_ROUTERS_QUERY_KEYS } from "./query-keys";

/**
 * The org's Model Routers (`/api/organizations/{orgId}/meta-profiles`). The
 * list only needs VIEW_ORG_SETTINGS, so every member can read it; the query is
 * disabled until an org is bound.
 */
export function useOrgModelRouters(orgId: string | null | undefined) {
  const { data: userIsAuthenticated } = useIsAuthed();

  return useQuery({
    queryKey: MODEL_ROUTERS_QUERY_KEYS.byOrg(orgId),
    queryFn: () => OrgModelRoutersService.list(orgId!),
    enabled: !!userIsAuthenticated && !!orgId,
    ...CONFIG_CACHE_OPTIONS,
    meta: { disableToast: true },
  });
}

/** One router's full configuration, fetched for the editor. */
export function useOrgModelRouter(
  orgId: string | null | undefined,
  name: string | null,
) {
  const { data: userIsAuthenticated } = useIsAuthed();

  return useQuery({
    queryKey: MODEL_ROUTERS_QUERY_KEYS.detail(orgId, name),
    queryFn: () => OrgModelRoutersService.get(orgId!, name!),
    enabled: !!userIsAuthenticated && !!orgId && !!name,
    ...CONFIG_CACHE_OPTIONS,
    meta: { disableToast: true },
  });
}
