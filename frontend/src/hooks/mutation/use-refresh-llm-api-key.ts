import type { UseMutationResult, DefaultError } from "@tanstack/react-query";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useLiteLlmIntegration } from "#/hooks/use-litellm-integration";
import { openHands } from "#/api/open-hands-axios";
import {
  LLM_API_KEY_QUERY_KEY,
  LlmApiKeyResponse,
} from "#/hooks/query/use-llm-api-key";

export function useRefreshLlmApiKey(): UseMutationResult<
  LlmApiKeyResponse,
  DefaultError,
  void,
  unknown
> {
  const { requireLiteLlm } = useLiteLlmIntegration();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async () => {
      requireLiteLlm();
      const { data } = await openHands.post<LlmApiKeyResponse>(
        "/api/keys/llm/byor/refresh",
      );
      return data;
    },
    onSuccess: () => {
      // Invalidate the LLM API key query to trigger a refetch
      queryClient.invalidateQueries({ queryKey: [LLM_API_KEY_QUERY_KEY] });
    },
  });
}
