import { useMutation, useQueryClient } from "@tanstack/react-query";
import { budgetService } from "#/api/budget-service/budget-service.api";
import type { BudgetOperation } from "#/api/budget-service/budget-service.types";
import {
  budgetNotificationKey,
  budgetOperationKey,
} from "#/hooks/query/use-budget-control";

export const useUpdateBudgetNotifications = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: budgetService.updateNotifications,
    retry: false,
    onSuccess: (preferences, variables) => {
      queryClient.setQueryData(
        budgetNotificationKey(variables.orgId),
        preferences,
      );
      return queryClient.invalidateQueries({
        queryKey: ["organizations", "budgets", variables.orgId],
      });
    },
  });
};

function useBudgetOperationMutation<T extends { orgId: string }>(
  mutationFn: (variables: T) => Promise<BudgetOperation>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    retry: false,
    onSuccess: (operation, variables) => {
      queryClient.setQueryData(
        budgetOperationKey(variables.orgId, operation.operation_id),
        operation,
      );
    },
    onSettled: (_data, _error, variables) =>
      queryClient.invalidateQueries({
        queryKey: ["organizations", "budgets", variables.orgId],
      }),
  });
}

export const useConfirmBudgetAdoption = () =>
  useBudgetOperationMutation(budgetService.confirm);

export const useUpdateBudgetPolicy = () =>
  useBudgetOperationMutation(budgetService.update);

export const useRetryBudgetOperation = () =>
  useBudgetOperationMutation(budgetService.retry);

export const useHandOffBudgetControl = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: budgetService.handOff,
    retry: false,
    onSettled: async (_data, _error, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["organizations", "budgets", variables.orgId],
        }),
        queryClient.invalidateQueries({
          queryKey: ["budget-control", variables.orgId, "operation"],
        }),
      ]);
    },
  });
};
