import type { UseQueryResult } from "@tanstack/react-query";
import { useQuery, DefaultError } from "@tanstack/react-query";
import { useLiteLlmIntegration } from "#/hooks/use-litellm-integration";
import { useConfig } from "./use-config";
import BillingService from "#/api/billing-service/billing-service.api";
import { useIsOnTosPage } from "#/hooks/use-is-on-tos-page";

export const useBalance = (): UseQueryResult<
  string | null | undefined,
  DefaultError
> => {
  const { data: config } = useConfig();
  const { enabled, guardQuery } = useLiteLlmIntegration();
  const isOnTosPage = useIsOnTosPage();

  const query = useQuery<
    Awaited<ReturnType<typeof BillingService.getBalance>>,
    DefaultError,
    Awaited<ReturnType<typeof BillingService.getBalance>> | undefined
  >({
    select: (data) => (enabled ? data : undefined),
    queryKey: ["user", "balance"],
    queryFn: guardQuery(() => BillingService.getBalance()),
    enabled:
      enabled &&
      !isOnTosPage &&
      config?.app_mode === "saas" &&
      config?.feature_flags?.enable_billing,
  });
  return query;
};
