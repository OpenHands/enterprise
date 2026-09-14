import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useBudgetOperation } from "#/hooks/query/use-budget-control";
import { useRetryBudgetOperation } from "#/hooks/mutation/use-budget-control";

interface BudgetOperationStatusProps {
  orgId: string;
  operationId: string;
  onAcknowledged?: () => void;
}

export function BudgetOperationStatus({
  orgId,
  operationId,
  onAcknowledged,
}: BudgetOperationStatusProps) {
  const { t } = useTranslation();
  const operation = useBudgetOperation(orgId, operationId);
  const retry = useRetryBudgetOperation();
  const status = operation.data?.status;
  const retryIsCurrent =
    retry.variables?.orgId === orgId &&
    retry.variables?.operationId === operationId;
  const messages = {
    pending: I18nKey.BUDGET_CONTROL$PENDING,
    applied: I18nKey.BUDGET_CONTROL$APPLIED,
    abandoned: I18nKey.BUDGET_CONTROL$ABANDONED,
  };

  return (
    <section className="space-y-3 rounded-lg border border-neutral-600 p-4">
      <p className="break-all text-sm text-neutral-400">
        {t(I18nKey.BUDGET_CONTROL$OPERATION, { id: operationId })}
      </p>
      <div aria-live="polite">
        {operation.isPending && <p>{t(I18nKey.HOME$LOADING)}</p>}
        {operation.isError ? (
          <p role="alert">{t(I18nKey.BUDGET_CONTROL$STATUS_UNAVAILABLE)}</p>
        ) : (
          status && <p>{t(messages[status])}</p>
        )}
        {retry.isError && retryIsCurrent && status === "pending" && (
          <p role="alert">{t(I18nKey.BUDGET_CONTROL$RETRY_FAILED)}</p>
        )}
      </div>
      <div className="flex gap-3">
        {(status === "applied" || status === "abandoned") &&
          !operation.isError &&
          onAcknowledged && (
            <button
              type="button"
              className="rounded border px-3 py-2"
              onClick={onAcknowledged}
            >
              {t(I18nKey.DEVICE$CONTINUE)}
            </button>
          )}
        {status === "pending" && (
          <button
            type="button"
            className="rounded bg-blue-600 px-3 py-2 disabled:opacity-50"
            disabled={(retry.isPending && retryIsCurrent) || operation.isError}
            onClick={() => retry.mutate({ orgId, operationId })}
          >
            {t(I18nKey.CONVERSATION$RETRY)}
          </button>
        )}
        {operation.isError && (
          <button
            type="button"
            className="rounded border px-3 py-2 disabled:opacity-50"
            disabled={operation.isFetching}
            onClick={() => operation.refetch()}
          >
            {t(I18nKey.BUTTON$REFRESH)}
          </button>
        )}
      </div>
    </section>
  );
}
