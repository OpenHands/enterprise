import { useQuery } from "@tanstack/react-query";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";

/**
 * Budget row of one service account. Budget rows list service accounts with
 * `account_type: "service"` and the user search matches their name, so the
 * row comes from the same budgets endpoint as the Budgets page. The key
 * starts with `["organizations", "budgets", orgId]`, so the budget
 * mutations refresh both.
 */
export const useServiceAccountBudget = (
  saId: string,
  name: string,
  enabled: boolean,
) => {
  const { organizationId } = useSelectedOrganizationId();

  return useQuery({
    queryKey: [
      "organizations",
      "budgets",
      organizationId,
      "service-account",
      saId,
      name,
    ],
    queryFn: async () => {
      const budgets = await organizationService.getBudgetSettings({
        orgId: organizationId!,
        usersPerPage: 50,
        usersSearch: name,
      });
      return budgets.users.find((row) => row.user_id === saId) ?? null;
    },
    enabled: enabled && !!organizationId,
  });
};
