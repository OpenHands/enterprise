import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import type { BudgetPreview } from "#/api/budget-service/budget-service.types";
import type { OrgBudgetSettings } from "#/api/organization-service/organization-service.api";
import type { BudgetSubmission } from "#/utils/budget-submission";
import {
  AllowanceDraft,
  buildBudgetSubmission,
  initialBudgetDraft,
} from "./budget-policy-draft";

function AllowanceField({
  label,
  value,
  onChange,
  member = false,
  inherit = false,
}: {
  label: string;
  value: AllowanceDraft;
  onChange: (value: AllowanceDraft) => void;
  member?: boolean;
  inherit?: boolean;
}) {
  const id = useId();
  const { t } = useTranslation();
  return (
    <div className="space-y-2">
      <label htmlFor={id} className="block text-sm">
        {label}
      </label>
      <input
        id={id}
        type="number"
        min="0"
        step="any"
        required={typeof value === "string"}
        disabled={value === null || value === undefined}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded border border-neutral-600 bg-transparent p-2 disabled:opacity-50"
      />
      {inherit && (
        <label className="flex gap-2 text-sm">
          <input
            type="checkbox"
            checked={value === undefined}
            onChange={(event) =>
              onChange(event.target.checked ? undefined : "")
            }
          />
          {t(I18nKey.BUDGET_CONTROL$INHERIT)}
        </label>
      )}
      {member && (
        <label className="flex gap-2 text-sm">
          <input
            type="checkbox"
            disabled={value === undefined}
            checked={value === null}
            onChange={(event) => onChange(event.target.checked ? null : "")}
          />
          {t(I18nKey.BUDGET_CONTROL$NO_MEMBER_LIMIT)}
        </label>
      )}
    </div>
  );
}

type PolicySource = { preview: BudgetPreview } | { budget: OrgBudgetSettings };
export function BudgetPolicyForm({
  source,
  disabled,
  onSubmit,
}: {
  source: PolicySource;
  disabled: boolean;
  onSubmit: (submission: BudgetSubmission) => void;
}) {
  const { t } = useTranslation();
  // Keep the reviewed generation/fingerprint stable while the admin edits.
  const [reviewed] = useState(source);
  const adopting = "preview" in reviewed;
  const [draft, setDraft] = useState(() =>
    initialBudgetDraft("budget" in reviewed ? reviewed.budget : undefined),
  );
  const [invalid, setInvalid] = useState(false);
  const currentLabel = t(
    adopting
      ? I18nKey.BUDGET_CONTROL$AVAILABLE_NOW
      : I18nKey.BUDGET_CONTROL$CYCLE_TOTAL,
  );
  const futureLabel = t(I18nKey.BUDGET_CONTROL$FUTURE_ALLOWANCE);
  const members =
    "preview" in source
      ? source.preview.members
          .filter((member) => member.in_organization)
          .map((member) => ({ id: member.user_id, name: member.user_id }))
      : source.budget.users.map((member) => ({
          id: member.user_id,
          name: member.user_name || member.user_email || member.user_id,
        }));
  return (
    <form
      className="space-y-6"
      onSubmit={(event) => {
        event.preventDefault();
        if (disabled) return;
        let submission: BudgetSubmission;
        try {
          submission = buildBudgetSubmission(
            draft,
            crypto.randomUUID(),
            reviewed,
          );
        } catch {
          setInvalid(true);
          return;
        }
        setInvalid(false);
        onSubmit(submission);
      }}
    >
      <fieldset disabled={disabled} className="space-y-6 disabled:opacity-60">
        {!adopting && (
          <label className="flex gap-2">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) =>
                setDraft({ ...draft, enabled: event.target.checked })
              }
            />
            {t(I18nKey.BUDGET_CONTROL$ENFORCE)}
          </label>
        )}
        <fieldset className="space-y-3">
          <legend className="font-semibold">
            {t(I18nKey.BUDGET_CONTROL$ORGANIZATION)}
          </legend>
          <div className="grid gap-4 md:grid-cols-2">
            <AllowanceField
              label={currentLabel}
              value={draft.currentTeam}
              onChange={(value) =>
                setDraft({ ...draft, currentTeam: value ?? "" })
              }
            />
            <AllowanceField
              label={futureLabel}
              value={draft.futureTeam}
              onChange={(value) =>
                setDraft({ ...draft, futureTeam: value ?? "" })
              }
            />
          </div>
        </fieldset>
        <fieldset className="space-y-3">
          <legend className="font-semibold">
            {t(I18nKey.BUDGET_CONTROL$DEFAULT_MEMBER)}
          </legend>
          <div className="grid gap-4 md:grid-cols-2">
            <AllowanceField
              member
              label={currentLabel}
              value={draft.currentDefault}
              onChange={(value) =>
                setDraft({ ...draft, currentDefault: value })
              }
            />
            <AllowanceField
              member
              label={futureLabel}
              value={draft.futureDefault}
              onChange={(value) => setDraft({ ...draft, futureDefault: value })}
            />
          </div>
        </fieldset>
        <label className="flex flex-wrap items-center gap-3">
          {t(I18nKey.BUDGET_CONTROL$RESET_DAY)}
          <select
            value={draft.resetDay}
            onChange={(event) =>
              setDraft({
                ...draft,
                resetDay: event.target.value === "15" ? 15 : 1,
              })
            }
            className="rounded border border-neutral-600 bg-neutral-900 p-2"
          >
            <option value="1">1</option>
            <option value="15">15</option>
          </select>
        </label>
        {adopting && (
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              checked={draft.replaceResets}
              onChange={(event) =>
                setDraft({ ...draft, replaceResets: event.target.checked })
              }
            />
            {t(I18nKey.BUDGET_CONTROL$REPLACE_RESETS)}
          </label>
        )}
        <details className="space-y-3">
          <summary className="cursor-pointer">
            {t(I18nKey.BUDGET_CONTROL$MEMBER_OVERRIDES)}
          </summary>
          {members.map((member) => (
            <fieldset
              key={member.id}
              className="rounded border border-neutral-700 p-3"
            >
              <legend className="break-all px-1">{member.name}</legend>
              <div className="grid gap-4 md:grid-cols-2">
                <AllowanceField
                  member
                  inherit
                  label={currentLabel}
                  value={draft.currentMembers[member.id]}
                  onChange={(value) =>
                    setDraft({
                      ...draft,
                      currentMembers: {
                        ...draft.currentMembers,
                        [member.id]: value,
                      },
                    })
                  }
                />
                <AllowanceField
                  member
                  inherit
                  label={futureLabel}
                  value={draft.futureMembers[member.id]}
                  onChange={(value) =>
                    setDraft({
                      ...draft,
                      futureMembers: {
                        ...draft.futureMembers,
                        [member.id]: value,
                      },
                    })
                  }
                />
              </div>
            </fieldset>
          ))}
        </details>
        {invalid && (
          <p role="alert">{t(I18nKey.BUDGET_CONTROL$INVALID_ALLOWANCE)}</p>
        )}
        <button
          type="submit"
          className="rounded bg-blue-600 px-4 py-2 disabled:opacity-50"
        >
          {t(adopting ? I18nKey.BUTTON$CONFIRM : I18nKey.SETTINGS$SAVE_CHANGES)}
        </button>
      </fieldset>
    </form>
  );
}
