import type { UseQueryResult } from "@tanstack/react-query";
import { useQuery, DefaultError } from "@tanstack/react-query";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { useLiteLlmIntegration } from "#/hooks/use-litellm-integration";

type BudgetQuery = Omit<
  Parameters<typeof organizationService.getBudgetSettings>[0],
  "orgId"
>;

export function useBudgetSettings({
  usersPage,
  usersPerPage,
  usersSearch,
  usersStatus,
}: BudgetQuery): UseQueryResult<
  Awaited<ReturnType<typeof organizationService.getBudgetSettings>> | undefined,
  DefaultError
> {
  const { organizationId } = useSelectedOrganizationId();
  const { enabled, guardQuery } = useLiteLlmIntegration();
  const query = useQuery<
    Awaited<ReturnType<typeof organizationService.getBudgetSettings>>,
    DefaultError,
    | Awaited<ReturnType<typeof organizationService.getBudgetSettings>>
    | undefined
  >({
    select: (data) => (enabled ? data : undefined),
    queryKey: [
      "organizations",
      "budgets",
      organizationId,
      usersPage,
      usersSearch ?? "",
      usersStatus,
      usersPerPage,
    ],
    queryFn: guardQuery(() => {
      if (!organizationId) throw new Error("An organization ID is required.");
      return organizationService.getBudgetSettings({
        usersPage,
        usersPerPage,
        usersSearch,
        usersStatus,
        orgId: organizationId,
      });
    }),
    enabled: enabled && !!organizationId,
  });
  return query;
}
