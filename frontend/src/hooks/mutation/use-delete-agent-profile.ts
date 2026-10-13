import { useMutation, useQueryClient } from "@tanstack/react-query";
import AgentProfilesService, {
  AgentProfileScope,
} from "#/api/agent-profiles-service/agent-profiles-service.api";
import { AGENT_PROFILES_QUERY_KEY } from "#/hooks/query/use-agent-profiles";

interface DeleteAgentProfileVariables {
  name: string;
  scope: AgentProfileScope;
}

export function useDeleteAgentProfile() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ name, scope }: DeleteAgentProfileVariables) => {
      await AgentProfilesService.deleteProfile(name, scope);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [AGENT_PROFILES_QUERY_KEY] });
    },
  });
}
