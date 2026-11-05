import { useQuery } from "@tanstack/react-query";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";

interface UseMyBudgetParams {
  /** `false` only asks whether budgets are enabled, without reading spend. */
  includeSpend?: boolean;
  enabled?: boolean;
  staleTime?: number;
}

export const useMyBudget = ({
  includeSpend = true,
  enabled = true,
  staleTime,
}: UseMyBudgetParams = {}) => {
  const { organizationId } = useSelectedOrganizationId();
  return useQuery({
    queryKey: ["organizations", "budgets", organizationId, "me", includeSpend],
    queryFn: () =>
      organizationService.getMyBudget({
        orgId: organizationId!,
        includeSpend,
      }),
    enabled: !!organizationId && enabled,
    staleTime,
    // The page renders its own error state, so a failure must stay quiet.
    retry: false,
    meta: { disableToast: true },
  });
};
