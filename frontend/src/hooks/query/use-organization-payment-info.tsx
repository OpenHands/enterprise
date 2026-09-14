import type { UseQueryResult } from "@tanstack/react-query";
import { useQuery, DefaultError } from "@tanstack/react-query";
import { useLiteLlmIntegration } from "#/hooks/use-litellm-integration";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";

export const useOrganizationPaymentInfo = (): UseQueryResult<
  { cardNumber: string } | undefined,
  DefaultError
> => {
  const { organizationId } = useSelectedOrganizationId();

  const { enabled, guardQuery } = useLiteLlmIntegration();
  const query = useQuery<
    Awaited<ReturnType<typeof organizationService.getOrganizationPaymentInfo>>,
    DefaultError,
    | Awaited<ReturnType<typeof organizationService.getOrganizationPaymentInfo>>
    | undefined
  >({
    select: (data) => (enabled ? data : undefined),
    queryKey: ["organizations", organizationId, "payment"],
    queryFn: guardQuery(() => {
      if (!organizationId) throw new Error("An organization ID is required.");
      return organizationService.getOrganizationPaymentInfo({
        orgId: organizationId,
      });
    }),
    enabled: enabled && !!organizationId,
  });
  return query;
};
