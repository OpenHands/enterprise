import type { DefaultError } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { useLiteLlmIntegration } from "#/hooks/use-litellm-integration";
import { openHands } from "#/api/open-hands-axios";
import { useConfig } from "./use-config";

export const LLM_API_KEY_QUERY_KEY = "llm-api-key";

export interface LlmApiKeyResponse {
  key: string | null;
}

export interface LlmApiKeyError {
  isPaymentRequired: boolean;
  message?: string;
}

export function useLlmApiKey(): {
  data: LlmApiKeyResponse | undefined;
  error: DefaultError | null;
  isLoading: boolean;
  isPaymentRequired: boolean;
} {
  const { data: config } = useConfig();
  const { enabled, guardQuery } = useLiteLlmIntegration();

  const query = useQuery({
    queryKey: [LLM_API_KEY_QUERY_KEY],
    // Fetch the BYOR key on SaaS, or whenever the deployment has explicitly
    // enabled BYOR export (e.g. self-hosted installs without billing).
    enabled:
      enabled &&
      (config?.app_mode === "saas" ||
        !!config?.feature_flags?.enable_byor_export),
    queryFn: guardQuery(async () => {
      const { data } =
        await openHands.get<LlmApiKeyResponse>("/api/keys/llm/byor");
      return data;
    }),
    staleTime: 1000 * 60 * 5, // 5 minutes
    gcTime: 1000 * 60 * 15, // 15 minutes
    retry: (failureCount, error) => {
      // Don't retry on 402 Payment Required
      if (error instanceof AxiosError && error.response?.status === 402) {
        return false;
      }
      return failureCount < 3;
    },
    // Disable global error toast - we handle 402 errors in the UI
    meta: { disableToast: true },
  });

  // Check if the error is a 402 Payment Required
  const isPaymentRequired =
    query.error instanceof AxiosError && query.error.response?.status === 402;

  return {
    data: enabled ? query.data : undefined,
    error: enabled ? query.error : null,
    isLoading: enabled && query.isLoading,
    isPaymentRequired: enabled && isPaymentRequired,
  };
}
