import type { UseMutationResult, DefaultError } from "@tanstack/react-query";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { useLiteLlmIntegration } from "#/hooks/use-litellm-integration";

type BudgetUpdate = Parameters<
  typeof organizationService.updateBudgetSettings
>[0]["payload"];
type BudgetOverride = Omit<
  Parameters<typeof organizationService.upsertBudgetOverride>[0],
  "orgId"
>;

export function useBudgetMutations(): {
  updateBudgets: UseMutationResult<
    Awaited<ReturnType<typeof organizationService.getBudgetSettings>>,
    DefaultError,
    BudgetUpdate,
    unknown
  >;
  upsertOverride: UseMutationResult<
    Awaited<ReturnType<typeof organizationService.upsertBudgetOverride>>,
    DefaultError,
    BudgetOverride,
    unknown
  >;
  deleteOverride: UseMutationResult<void, DefaultError, string, unknown>;
} {
  const { organizationId } = useSelectedOrganizationId();
  const { requireLiteLlm } = useLiteLlmIntegration();
  const queryClient = useQueryClient();
  const onSettled = (): Promise<void> =>
    queryClient.invalidateQueries({
      queryKey: ["organizations", "budgets", organizationId],
    });
  const updateBudgets = useMutation({
    mutationFn: (payload: BudgetUpdate) => {
      requireLiteLlm();
      if (!organizationId) throw new Error("An organization ID is required.");
      return organizationService.updateBudgetSettings({
        orgId: organizationId,
        payload,
      });
    },
    onSettled,
  });
  const upsertOverride = useMutation({
    mutationFn: (params: BudgetOverride) => {
      requireLiteLlm();
      if (!organizationId) throw new Error("An organization ID is required.");
      return organizationService.upsertBudgetOverride({
        ...params,
        orgId: organizationId,
      });
    },
    onSettled,
  });
  const deleteOverride = useMutation({
    mutationFn: (userId: string) => {
      requireLiteLlm();
      if (!organizationId) throw new Error("An organization ID is required.");
      return organizationService.deleteBudgetOverride({
        orgId: organizationId,
        userId,
      });
    },
    onSettled,
  });
  return { updateBudgets, upsertOverride, deleteOverride };
}
