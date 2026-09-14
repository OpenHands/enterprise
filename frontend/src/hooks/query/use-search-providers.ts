import type { UseQueryResult, DefaultError } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { useConfig } from "./use-config";
import ConfigService from "#/api/config-service/config-service.api";
import type { LLMProvider } from "#/api/config-service/config-service.types";

async function fetchAllProviders(): Promise<LLMProvider[]> {
  // Providers are a small set; fetch all in one call with a high limit.
  const page = await ConfigService.searchProviders({ limit: 100 });
  return page.items;
}

export const useSearchProviders = (): UseQueryResult<
  LLMProvider[],
  DefaultError
> => {
  const { data: config } = useConfig();
  return useQuery({
    queryKey: ["config", "providers"],
    queryFn: fetchAllProviders,
    select: (providers) =>
      config?.feature_flags?.enable_litellm === false
        ? providers.filter(
            (provider) =>
              !["openhands", "litellm_proxy"].includes(provider.name),
          )
        : providers,
    staleTime: 1000 * 60 * 5,
    gcTime: 1000 * 60 * 15,
  });
};
