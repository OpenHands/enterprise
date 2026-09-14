import { useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { WebClientConfig } from "#/api/option-service/option.types";
import { useConfig } from "#/hooks/query/use-config";
import { QUERY_KEYS } from "#/hooks/query/query-keys";
import { isLiteLlmEnabled } from "#/utils/litellm-capability";

export interface LiteLlmIntegration {
  enabled: boolean;
  isLoading: boolean;
  requireLiteLlm: () => void;
  guardQuery: <Output>(
    operation: () => Promise<Output>,
  ) => () => Promise<Output>;
}

export function useLiteLlmIntegration(): LiteLlmIntegration {
  const { data: config } = useConfig();
  const queryClient = useQueryClient();
  const configRef = useRef(config);
  configRef.current = config;

  // Check again at dispatch: disabled queries can still be manually refetched,
  // and a mounted mutation can outlive the configuration that enabled its UI.
  const requireLiteLlm = (): void => {
    const currentConfig =
      queryClient.getQueryData<WebClientConfig>(QUERY_KEYS.WEB_CLIENT_CONFIG) ??
      configRef.current;
    if (!isLiteLlmEnabled(currentConfig)) {
      throw new Error("The LiteLLM integration is disabled");
    }
  };

  const guardQuery =
    <Output>(operation: () => Promise<Output>): (() => Promise<Output>) =>
    (): Promise<Output> => {
      requireLiteLlm();
      return operation();
    };

  return {
    guardQuery,
    enabled: isLiteLlmEnabled(config),
    isLoading: !config,
    requireLiteLlm,
  };
}
