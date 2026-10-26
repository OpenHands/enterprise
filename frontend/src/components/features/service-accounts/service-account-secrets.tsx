import React from "react";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { CustomSecretWithoutValue } from "#/api/secrets-service.types";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { OptionalTag } from "#/components/features/settings/optional-tag";
import { useServiceAccountSecrets } from "#/hooks/query/use-service-account-secrets";
import {
  useDeleteServiceAccountSecret,
  usePutServiceAccountSecret,
} from "#/hooks/mutation/use-service-account-secret-mutations";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";
import {
  formControlMultilineFieldClassName,
  formControlSettingsFieldClassName,
} from "#/utils/form-control-classes";
import {
  settingsListIconActionButtonClassName,
  settingsListRowClassName,
} from "#/utils/settings-list-classes";
import EditIcon from "#/icons/u-edit.svg?react";
import DeleteIcon from "#/icons/u-delete.svg?react";

/** `null` is "add a new secret"; a secret is "edit" (its value is replaced). */
type FormState =
  | { mode: "add" }
  | { mode: "edit"; secret: CustomSecretWithoutValue };

function SecretForm({
  state,
  existingNames,
  isSaving,
  onSubmit,
  onCancel,
}: {
  state: FormState;
  existingNames: string[];
  isSaving: boolean;
  onSubmit: (secret: {
    name: string;
    value: string;
    description?: string;
  }) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const isEdit = state.mode === "edit";
  const [name, setName] = React.useState(isEdit ? state.secret.name : "");
  const [value, setValue] = React.useState("");
  const [description, setDescription] = React.useState(
    isEdit ? (state.secret.description ?? "") : "",
  );
  const isNameTaken = !isEdit && existingNames.includes(name);

  return (
    <form
      data-testid="service-account-secret-form"
      className="flex flex-col items-start gap-4 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({
          name,
          value,
          // Omitting the description keeps the stored one.
          description: description || undefined,
        });
      }}
    >
      <SettingsInput
        testId="service-account-secret-name-input"
        label={t(I18nKey.SETTINGS$NAME)}
        type="text"
        className="max-w-[350px]"
        value={name}
        onChange={setName}
        isDisabled={isEdit}
        required
        pattern="^[A-Za-z_][A-Za-z0-9_]*$"
        error={
          isNameTaken ? t(I18nKey.SECRETS$SECRET_ALREADY_EXISTS) : undefined
        }
      />
      <label className="flex w-full max-w-[680px] flex-col gap-2.5">
        <span className="text-sm">
          {t(
            isEdit
              ? I18nKey.SERVICE_ACCOUNTS$SECRET_NEW_VALUE
              : I18nKey.FORM$VALUE,
          )}
        </span>
        <textarea
          data-testid="service-account-secret-value-input"
          required
          rows={4}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          autoComplete="off"
          className={cn(formControlMultilineFieldClassName, "resize-none")}
        />
      </label>
      <label className="flex w-full max-w-[680px] flex-col gap-2.5">
        <div className="flex items-center gap-2">
          <span className="text-sm">{t(I18nKey.FORM$DESCRIPTION)}</span>
          <OptionalTag />
        </div>
        <input
          data-testid="service-account-secret-description-input"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          className={formControlSettingsFieldClassName}
        />
      </label>
      <div className="flex items-center gap-4">
        <BrandButton type="button" variant="secondary" onClick={onCancel}>
          {t(I18nKey.BUTTON$CANCEL)}
        </BrandButton>
        <BrandButton
          testId="service-account-secret-submit-button"
          type="submit"
          variant="primary"
          isDisabled={isSaving || isNameTaken || !name || !value}
        >
          {t(isEdit ? I18nKey.SECRETS$EDIT_SECRET : I18nKey.SECRETS$ADD_SECRET)}
        </BrandButton>
      </div>
    </form>
  );
}

/**
 * Secrets of one service account: add, replace the value, delete. The API
 * returns names and descriptions only, so a value is never shown.
 */
export function ServiceAccountSecrets({ saId }: { saId: string }) {
  const { t } = useTranslation();
  const { data: secrets = [] } = useServiceAccountSecrets(saId);
  const { mutate: putSecret, isPending: isSaving } =
    usePutServiceAccountSecret(saId);
  const { mutate: deleteSecret, isPending: isDeleting } =
    useDeleteServiceAccountSecret(saId);
  const [form, setForm] = React.useState<FormState | null>(null);

  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <h4 className="text-sm font-medium">
            {t(I18nKey.SERVICE_ACCOUNTS$SECRETS_HEADER)}
          </h4>
          <p className="text-xs text-muted">
            {t(I18nKey.SERVICE_ACCOUNTS$SECRETS_HELP)}
          </p>
        </div>
        {!form && (
          <BrandButton
            testId="add-service-account-secret-button"
            type="button"
            variant="secondary"
            className="shrink-0"
            onClick={() => setForm({ mode: "add" })}
            startContent={<Plus size={14} />}
          >
            {t(I18nKey.SECRETS$ADD_SECRET)}
          </BrandButton>
        )}
      </div>

      {form ? (
        <SecretForm
          state={form}
          existingNames={secrets.map((secret) => secret.name)}
          isSaving={isSaving}
          onCancel={() => setForm(null)}
          onSubmit={(secret) =>
            putSecret(secret, { onSuccess: () => setForm(null) })
          }
        />
      ) : (
        <ul>
          {secrets.length === 0 && (
            <li className="py-2 text-sm text-muted">
              {t(I18nKey.SERVICE_ACCOUNTS$SECRETS_EMPTY)}
            </li>
          )}
          {secrets.map((secret) => (
            <li
              key={secret.name}
              data-testid="service-account-secret-item"
              className={cn(settingsListRowClassName, "justify-between gap-4")}
            >
              <span className="min-w-0 truncate text-sm">{secret.name}</span>
              <span className="min-w-0 flex-1 truncate text-sm text-muted">
                {secret.description}
              </span>
              <div className="flex items-center gap-0.5">
                <button
                  data-testid="edit-service-account-secret"
                  type="button"
                  aria-label={t(I18nKey.SERVICE_ACCOUNTS$ACTION_LABEL, {
                    action: t(I18nKey.BUTTON$EDIT),
                    name: secret.name,
                  })}
                  className={settingsListIconActionButtonClassName}
                  onClick={() => setForm({ mode: "edit", secret })}
                >
                  <EditIcon width={16} height={16} />
                </button>
                <button
                  data-testid="delete-service-account-secret"
                  type="button"
                  aria-label={t(I18nKey.SERVICE_ACCOUNTS$ACTION_LABEL, {
                    action: t(I18nKey.BUTTON$DELETE),
                    name: secret.name,
                  })}
                  disabled={isDeleting}
                  className={settingsListIconActionButtonClassName}
                  onClick={() => deleteSecret({ name: secret.name })}
                >
                  <DeleteIcon width={16} height={16} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
