import { useState } from "react";
import { useTranslation } from "react-i18next";
import { isAxiosError } from "axios";
import { I18nKey } from "#/i18n/declaration";
import type { OrgBudgetSettings } from "#/api/organization-service/organization-service.api";
import { useBudgetAdoptionPreview } from "#/hooks/query/use-budget-control";
import { useBudgetSubmission } from "#/hooks/mutation/use-budget-submission";
import { useHandOffBudgetControl } from "#/hooks/mutation/use-budget-control";
import {
  clearBudgetSubmission,
  readBudgetSubmission,
  clearBudgetSubmissionAfterHandoff,
  BudgetSubmission,
} from "#/utils/budget-submission";
import { BudgetOperationStatus } from "./budget-operation-status";
import { BudgetPolicyForm } from "./budget-policy-form";

function loadSubmission(orgId: string) {
  try {
    return { saved: readBudgetSubmission(orgId), unavailable: false };
  } catch {
    return { saved: null, unavailable: true };
  }
}

function responseMessage(error: unknown): string | null {
  if (!isAxiosError(error)) return null;
  const detail = error.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return typeof detail?.message === "string" ? detail.message : null;
}

// The parent keys this component by organization so forms never cross tenants.
export function BudgetControlPanel({
  orgId,
  budget,
  disabled = false,
}: {
  orgId: string;
  budget: OrgBudgetSettings;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [recovery, setRecovery] = useState(() => loadSubmission(orgId));
  const [reviewing, setReviewing] = useState(false);
  const [formRevision, setFormRevision] = useState(0);
  const [handoffGeneration, setHandoffGeneration] = useState<number | null>(
    null,
  );
  const preview = useBudgetAdoptionPreview(orgId, reviewing);
  const submission = useBudgetSubmission();
  const handoff = useHandOffBudgetControl();
  const modeLabels = {
    managed: I18nKey.BUDGET_CONTROL$MANAGED,
    external: I18nKey.BUDGET_CONTROL$EXTERNAL,
    needs_adoption: I18nKey.BUDGET_CONTROL$NEEDS_ADOPTION,
  };
  const mode = budget.control_mode;
  const operationId =
    budget.pending_operation_id ??
    recovery.saved?.operationId ??
    (reviewing ? preview.data?.pending_operation_id : null);
  const unresolved = !!operationId || !!recovery.saved || recovery.unavailable;
  const locked =
    disabled || submission.isPending || handoff.isPending || unresolved;
  const submit = (next: BudgetSubmission) => {
    submission.mutate(
      { orgId, submission: next },
      {
        onSettled: () => setRecovery(loadSubmission(orgId)),
      },
    );
  };
  const acknowledge = () => {
    try {
      if (recovery.saved)
        clearBudgetSubmission(
          orgId,
          recovery.saved.submission.request.idempotency_key,
        );
    } catch {
      setRecovery({ saved: recovery.saved, unavailable: true });
      return;
    }
    setRecovery(loadSubmission(orgId));
    submission.reset();
    setReviewing(false);
    setFormRevision((revision) => revision + 1);
  };

  return (
    <section className="space-y-6">
      <h2 className="text-xl font-semibold">
        {t(modeLabels[mode] ?? I18nKey.BUDGET_CONTROL$NEEDS_ADOPTION)}
      </h2>
      {mode !== "managed" && !operationId && !recovery.saved && (
        <p>{t(I18nKey.BUDGET_CONTROL$EXTERNAL_EXPLANATION)}</p>
      )}
      {recovery.unavailable && (
        <p role="alert">{t(I18nKey.BUDGET_CONTROL$RECOVERY_UNAVAILABLE)}</p>
      )}
      {submission.isError && (
        <p role="alert">
          {responseMessage(submission.error) ??
            t(I18nKey.BUDGET_CONTROL$REQUEST_ERROR)}
        </p>
      )}
      {operationId && (!recovery.saved || recovery.saved.operationId) && (
        <BudgetOperationStatus
          orgId={orgId}
          operationId={operationId}
          onAcknowledged={recovery.saved ? acknowledge : undefined}
        />
      )}
      {recovery.saved && !recovery.saved.operationId && (
        <div className="space-y-3 rounded border border-amber-600 p-4">
          <p>{t(I18nKey.BUDGET_CONTROL$UNCERTAIN_REQUEST)}</p>
          <button
            type="button"
            disabled={submission.isPending || handoff.isPending}
            onClick={() => submit(recovery.saved!.submission)}
            className="rounded border px-3 py-2 disabled:opacity-50"
          >
            {t(I18nKey.CONVERSATION$RETRY)}
          </button>
        </div>
      )}
      {!locked && mode !== "managed" && (
        <button
          type="button"
          disabled={preview.isFetching}
          onClick={() => {
            setReviewing(true);
            preview.refetch();
            setFormRevision((revision) => revision + 1);
          }}
          className="rounded border px-3 py-2 disabled:opacity-50"
        >
          {t(I18nKey.BUDGET_CONTROL$PREVIEW)}
        </button>
      )}
      {reviewing && preview.isFetching && <p>{t(I18nKey.HOME$LOADING)}</p>}
      {reviewing && preview.isError && (
        <p role="alert">
          {responseMessage(preview.error) ??
            t(I18nKey.BUDGET_CONTROL$STATUS_UNAVAILABLE)}
        </p>
      )}
      {reviewing && preview.data && !preview.isError && !preview.isFetching && (
        <details className="rounded border border-neutral-600 p-3">
          <summary className="cursor-pointer">
            {t(I18nKey.BUDGET_CONTROL$NATIVE_POLICY)}
          </summary>
          <p className="my-3 text-sm">
            {t(I18nKey.BUDGET_CONTROL$INDEPENDENT_LIMITS)}
          </p>
          <pre className="max-h-96 overflow-auto text-xs">
            {JSON.stringify(preview.data, null, 2)}
          </pre>
        </details>
      )}
      {!unresolved && mode === "managed" && (
        <BudgetPolicyForm
          key={`managed:${formRevision}`}
          source={{ budget }}
          disabled={locked}
          onSubmit={submit}
        />
      )}
      {!locked &&
        reviewing &&
        mode !== "managed" &&
        preview.data &&
        !preview.isError &&
        !preview.isFetching && (
          <BudgetPolicyForm
            key={`${preview.data.fingerprint}:${formRevision}`}
            source={{ preview: preview.data }}
            disabled={locked}
            onSubmit={submit}
          />
        )}
      <div className="space-y-3 border-t border-neutral-700 pt-4">
        <label className="flex items-start gap-2">
          <input
            type="checkbox"
            checked={handoffGeneration !== null}
            disabled={submission.isPending || handoff.isPending}
            onChange={(event) =>
              setHandoffGeneration(
                event.target.checked ? budget.control_generation : null,
              )
            }
          />
          {t(I18nKey.BUDGET_CONTROL$HANDOFF_WARNING)}
        </label>
        <button
          type="button"
          disabled={
            handoffGeneration === null ||
            submission.isPending ||
            handoff.isPending
          }
          className="rounded border px-3 py-2 disabled:opacity-50"
          onClick={() => {
            if (handoffGeneration === null) return;
            handoff.mutate(
              { orgId, request: { expected_generation: handoffGeneration } },
              {
                onSuccess: () => {
                  try {
                    clearBudgetSubmissionAfterHandoff(orgId);
                  } catch {
                    setRecovery({ saved: recovery.saved, unavailable: true });
                    return;
                  }
                  setRecovery(loadSubmission(orgId));
                  submission.reset();
                  setReviewing(false);
                  setHandoffGeneration(null);
                },
              },
            );
          }}
        >
          {t(I18nKey.BUDGET_CONTROL$HANDOFF)}
        </button>
        {handoff.isError && (
          <p role="alert">
            {responseMessage(handoff.error) ??
              t(I18nKey.BUDGET_CONTROL$REQUEST_ERROR)}
          </p>
        )}
      </div>
    </section>
  );
}
