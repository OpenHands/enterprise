import { useMutation, useQueryClient } from "@tanstack/react-query";
import AgentProfilesService from "#/api/agent-profiles-service/agent-profiles-service.api";
import { AGENT_PROFILES_QUERY_KEY } from "#/hooks/query/use-agent-profiles";

/** Set (``isDefault``) or clear the organization's default agent profile. */
export function useSetOrgDefaultAgentProfile() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      id,
      isDefault,
    }: {
      id: string;
      isDefault: boolean;
    }) => {
      if (isDefault) {
        await AgentProfilesService.setOrgDefaultProfile(id);
      } else {
        await AgentProfilesService.clearOrgDefaultProfile(id);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [AGENT_PROFILES_QUERY_KEY] });
    },
  });
}
