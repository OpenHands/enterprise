import { useMutation, useQueryClient } from "@tanstack/react-query";
import { openHands } from "#/api/open-hands-axios";
import {
  LLM_API_KEY_QUERY_KEY,
  LlmApiKeyResponse,
} from "#/hooks/query/use-llm-api-key";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";

export function useRefreshLlmApiKey() {
  const queryClient = useQueryClient();
  const { organizationId } = useSelectedOrganizationId();

  return useMutation({
    mutationFn: async () => {
      const { data } = await openHands.post<LlmApiKeyResponse>(
        "/api/keys/llm/byor/refresh",
      );
      return data;
    },
    onSuccess: () => {
      // Invalidate the LLM API key query to trigger a refetch
      queryClient.invalidateQueries({
        queryKey: [LLM_API_KEY_QUERY_KEY, organizationId],
      });
    },
  });
}
