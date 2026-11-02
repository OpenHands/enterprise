import { useQuery } from "@tanstack/react-query";
import AgentProfilesService from "#/api/agent-profiles-service/agent-profiles-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { useIsAuthed } from "./use-is-authed";

export const AGENT_PROFILES_QUERY_KEY = "agent-profiles";

export function useAgentProfiles() {
  const { data: userIsAuthenticated } = useIsAuthed();
  // Personal profiles are per (org, member), so the cache is keyed by org.
  const { organizationId } = useSelectedOrganizationId();

  return useQuery({
    queryKey: [AGENT_PROFILES_QUERY_KEY, organizationId],
    queryFn: AgentProfilesService.listProfiles,
    enabled: !!userIsAuthenticated,
    staleTime: 1000 * 60 * 5,
    gcTime: 1000 * 60 * 15,
    meta: { disableToast: true },
  });
}
