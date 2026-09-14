import { WebClientConfig } from "#/api/option-service/option.types";

/** Wait for configuration; older servers omit the flag and remain enabled. */
export const isLiteLlmEnabled = (config?: WebClientConfig): boolean =>
  !!config && config.feature_flags?.enable_litellm !== false;

export const isManagedLlmModel = (model: string): boolean =>
  /^(openhands|litellm_proxy)\//i.test(model.trim());
