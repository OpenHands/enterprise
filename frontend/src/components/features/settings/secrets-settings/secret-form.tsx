import { useQueryClient } from "@tanstack/react-query";
import React from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useCreateSecret } from "#/hooks/mutation/use-create-secret";
import { useUpdateSecret } from "#/hooks/mutation/use-update-secret";
import { SettingsInput } from "../settings-input";
import { cn } from "#/utils/utils";
import {
  formControlMultilineFieldClassName,
  formControlSettingsFieldClassName,
} from "#/utils/form-control-classes";
import { BrandButton } from "../brand-button";
import { useSearchSecrets } from "#/hooks/query/use-get-secrets";
import { OptionalTag } from "../optional-tag";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { useMe } from "#/hooks/query/use-me";
import { usePermission } from "#/hooks/organizations/use-permissions";

interface SecretFormProps {
  mode: "add" | "edit";
  selectedSecret: string | null;
  onCancel: () => void;
}

export function SecretForm({
  mode,
  selectedSecret,
  onCancel,
}: SecretFormProps) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();
  const { organizationId } = useSelectedOrganizationId();
  const { data: me } = useMe();
  const { hasPermission } = usePermission(me?.role ?? "member");
  const canShareWithOrg = hasPermission("manage_org_secrets");

  const { data: secrets } = useSearchSecrets();
  const { mutate: createSecret } = useCreateSecret();
  const { mutate: updateSecret } = useUpdateSecret();

  const [error, setError] = React.useState<string | null>(null);

  const selectedSecretObj =
    mode === "edit" && selectedSecret
      ? secrets?.find((secret) => secret.name === selectedSecret)
      : undefined;
  const selectedSecretIsShared = selectedSecretObj?.scope === "organization";

  const secretDescription =
    (selectedSecretObj?.description?.trim() as string) || "";

  const invalidateSecrets = () => {
    // Invalidate both the new infinite query and the legacy query for compatibility
    queryClient.invalidateQueries({
      queryKey: ["secrets-search"],
    });
    queryClient.invalidateQueries({
      queryKey: ["secrets", organizationId],
    });
  };

  const handleCreateSecret = (
    name: string,
    value: string,
    description: string | undefined,
    isShared: boolean,
  ) => {
    createSecret(
      {
        name,
        value,
        description,
        isShared,
        organizationId,
      },
      {
        onSettled: onCancel,
        onSuccess: invalidateSecrets,
      },
    );
  };

  const handleEditSecret = (
    secretToEdit: string,
    name: string,
    description: string | undefined,
    isShared: boolean,
  ) => {
    updateSecret(
      {
        secretToEdit,
        name,
        description,
        isShared,
        organizationId,
      },
      {
        onSettled: onCancel,
        onSuccess: invalidateSecrets,
      },
    );
  };

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const formData = new FormData(event.currentTarget);
    const name = formData.get("secret-name")?.toString();
    const value = formData.get("secret-value")?.toString().trim();
    const description = formData.get("secret-description")?.toString();
    const isShared =
      formData.get("secret-shared")?.toString() === "on" ||
      formData.get("secret-shared") === "on";

    if (name) {
      setError(null);

      const isNameAlreadyUsed = secrets?.some(
        (secret) => secret.name === name && secret.name !== selectedSecret,
      );
      if (isNameAlreadyUsed) {
        setError(t("SECRETS$SECRET_ALREADY_EXISTS"));
        return;
      }

      if (mode === "add") {
        if (!value) {
          setError(t("SECRETS$SECRET_VALUE_REQUIRED"));
          return;
        }

        handleCreateSecret(name, value, description || undefined, isShared);
      } else if (mode === "edit" && selectedSecret) {
        handleEditSecret(
          selectedSecret,
          name,
          description || undefined,
          isShared,
        );
      }
    }
  };

  const formTestId = mode === "add" ? "add-secret-form" : "edit-secret-form";

  return (
    <form
      data-testid={formTestId}
      onSubmit={handleSubmit}
      className="flex flex-col items-start gap-6"
    >
      <SettingsInput
        testId="name-input"
        name="secret-name"
        type="text"
        label="Name"
        className="w-full max-w-[350px]"
        required
        defaultValue={mode === "edit" && selectedSecret ? selectedSecret : ""}
        placeholder={t("SECRETS$API_KEY_EXAMPLE")}
        pattern="^\S*$"
      />
      {error && <p className="text-red-500 text-sm">{error}</p>}

      {mode === "add" && (
        <label className="flex flex-col gap-2.5 w-full max-w-[680px]">
          <span className="text-sm">{t(I18nKey.FORM$VALUE)}</span>
          <textarea
            data-testid="value-input"
            name="secret-value"
            required
            className={cn(
              formControlMultilineFieldClassName,
              "resize-none",
              "disabled:bg-[var(--oh-surface-raised)] disabled:border-[var(--oh-border-subtle)]",
            )}
            rows={8}
          />
        </label>
      )}

      <label className="flex flex-col gap-2.5 w-full max-w-[680px]">
        <div className="flex items-center gap-2">
          <span className="text-sm">{t(I18nKey.FORM$DESCRIPTION)}</span>
          <OptionalTag />
        </div>
        <input
          data-testid="description-input"
          name="secret-description"
          defaultValue={secretDescription}
          className={cn(
            formControlSettingsFieldClassName,
            "disabled:bg-[var(--oh-surface-raised)] disabled:border-[var(--oh-border-subtle)]",
          )}
        />
      </label>

      {canShareWithOrg && (
        <div
          data-testid="share-with-org-label"
          className="flex items-start gap-2.5 w-full max-w-[680px]"
        >
          <input
            id="secret-shared"
            data-testid="share-with-org-checkbox"
            name="secret-shared"
            type="checkbox"
            defaultChecked={selectedSecretIsShared}
            className="mt-0.5 size-4 cursor-pointer accent-[var(--oh-brand)]"
          />
          <label
            htmlFor="secret-shared"
            className="flex flex-1 flex-col gap-1 cursor-pointer"
          >
            <span className="text-sm">
              {t(I18nKey.SECRETS$SHARE_WITH_ORGANIZATION)}
            </span>
            <span className="text-xs text-muted">
              {t(I18nKey.SECRETS$SHARE_WITH_ORGANIZATION_DESCRIPTION)}
            </span>
          </label>
        </div>
      )}

      <div className="flex items-center gap-4">
        <BrandButton
          testId="cancel-button"
          type="button"
          variant="secondary"
          onClick={onCancel}
        >
          {t(I18nKey.BUTTON$CANCEL)}
        </BrandButton>
        <BrandButton testId="submit-button" type="submit" variant="primary">
          {mode === "add" && t("SECRETS$ADD_SECRET")}
          {mode === "edit" && t("SECRETS$EDIT_SECRET")}
        </BrandButton>
      </div>
    </form>
  );
}
