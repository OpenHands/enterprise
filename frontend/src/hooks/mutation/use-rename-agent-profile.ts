import { useMutation, useQueryClient } from "@tanstack/react-query";
import AgentProfilesService, {
  AgentProfileScope,
} from "#/api/agent-profiles-service/agent-profiles-service.api";
import { AGENT_PROFILES_QUERY_KEY } from "#/hooks/query/use-agent-profiles";

interface RenameAgentProfileVariables {
  name: string;
  newName: string;
  scope: AgentProfileScope;
}

export function useRenameAgentProfile() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      name,
      newName,
      scope,
    }: RenameAgentProfileVariables) => {
      await AgentProfilesService.renameProfile(name, newName, scope);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [AGENT_PROFILES_QUERY_KEY] });
    },
  });
}
