import { useMutation, useQueryClient } from "@tanstack/react-query";
import AgentProfilesService from "#/api/agent-profiles-service/agent-profiles-service.api";
import { AGENT_PROFILES_QUERY_KEY } from "#/hooks/query/use-agent-profiles";

export function useActivateAgentProfile() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (id: string) => {
      await AgentProfilesService.activateProfile(id);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [AGENT_PROFILES_QUERY_KEY] });
    },
  });
}
