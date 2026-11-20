import { useQuery } from "@tanstack/react-query";
import AgentProfilesService from "#/api/agent-profiles-service/agent-profiles-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import {
  AGENT_PROFILES_QUERY_KEY,
  useAgentProfiles,
} from "#/hooks/query/use-agent-profiles";

export interface EffectiveAgentProfileSandboxSpec {
  profileName: string;
  specId: string;
}

/**
 * The sandbox spec a new sandbox inherits when the member saved no default
 * spec: that of their effective agent profile (their own selection, else the
 * organization default), when the deployment still offers it. ``null``
 * means the system default applies. Read-only: never a saved preference.
 */
export function useEffectiveAgentProfileSandboxSpec(
  offeredSpecIds: string[] | undefined,
  enabled: boolean,
): EffectiveAgentProfileSandboxSpec | null {
  const { organizationId } = useSelectedOrganizationId();
  const { data: listing } = useAgentProfiles({ enabled });
  const activeId = listing?.active_agent_profile_id ?? null;
  const summary =
    listing?.profiles.find(({ id }) => !!id && id === activeId) ?? null;

  // Under the list's key, so profile mutations refresh it too.
  const { data: detail } = useQuery({
    queryKey: [
      AGENT_PROFILES_QUERY_KEY,
      organizationId,
      "detail",
      summary?.scope,
      summary?.name,
    ],
    queryFn: () =>
      AgentProfilesService.getProfile(summary!.name, summary!.scope),
    enabled: enabled && !!summary,
    staleTime: 1000 * 60 * 5,
    meta: { disableToast: true },
  });

  const specId = detail?.profile.sandbox?.spec_id;
  if (!enabled || !summary || !specId || detail?.profile.id !== summary.id) {
    return null;
  }
  // A retired sandbox spec is skipped at launch, so it is not inherited either.
  if (!offeredSpecIds?.includes(specId)) return null;
  return { profileName: summary.name, specId };
}
