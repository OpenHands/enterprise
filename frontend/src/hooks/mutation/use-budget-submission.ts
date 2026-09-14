import { useMutation, useQueryClient } from "@tanstack/react-query";
import { budgetService } from "#/api/budget-service/budget-service.api";
import { budgetOperationKey } from "#/hooks/query/use-budget-control";
import {
  BudgetSubmission,
  saveBudgetSubmission,
} from "#/utils/budget-submission";

export function useBudgetSubmission() {
  const client = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: async ({
      orgId,
      submission,
    }: {
      orgId: string;
      submission: BudgetSubmission;
    }) => {
      // Keep the original request through a lost response or browser reload.
      saveBudgetSubmission(orgId, { submission, operationId: null });
      const operation =
        submission.kind === "adopt"
          ? await budgetService.confirm({ orgId, request: submission.request })
          : await budgetService.update({ orgId, request: submission.request });
      client.setQueryData(
        budgetOperationKey(orgId, operation.operation_id),
        operation,
      );
      saveBudgetSubmission(orgId, {
        submission,
        operationId: operation.operation_id,
      });
      return operation;
    },
    onSettled: (_data, _error, variables) =>
      client.invalidateQueries({
        queryKey: ["organizations", "budgets", variables.orgId],
      }),
  });
}
