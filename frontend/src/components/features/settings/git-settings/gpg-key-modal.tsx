import React from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { ModalBackdrop } from "#/components/shared/modals/modal-backdrop";
import { ModalBody } from "#/components/shared/modals/modal-body";
import {
  BaseModalTitle,
  BaseModalDescription,
} from "#/components/shared/modals/confirmation-modals/base-modal";
import { BrandButton } from "#/components/features/settings/brand-button";
import { useCreateSecret } from "#/hooks/mutation/use-create-secret";
import { useSearchSecrets } from "#/hooks/query/use-get-secrets";
import { I18nKey } from "#/i18n/declaration";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { cn } from "#/utils/utils";

const GPG_SECRET_NAME = "GPG_KEY";

interface GpgKeyModalProps {
  onClose: () => void;
  onSaved: () => void;
}

export function GpgKeyModal({ onClose, onSaved }: GpgKeyModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { organizationId } = useSelectedOrganizationId();
  const { mutate: createSecret, isPending } = useCreateSecret();
  const { data: secrets } = useSearchSecrets();

  const [error, setError] = React.useState<string | null>(null);

  const existingSecret = secrets?.find(
    (secret) => secret.name === GPG_SECRET_NAME,
  );

  const invalidateSecrets = () => {
    queryClient.invalidateQueries({ queryKey: ["secrets-search"] });
    queryClient.invalidateQueries({ queryKey: ["secrets", organizationId] });
  };

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const value = formData.get("gpg-key-value")?.toString().trim();

    if (!value) {
      setError(t(I18nKey.SECRETS$SECRET_VALUE_REQUIRED));
      return;
    }

    setError(null);
    createSecret(
      {
        name: GPG_SECRET_NAME,
        value,
        description: t(I18nKey.SETTINGS$GPG_KEY_DESCRIPTION),
      },
      {
        onSuccess: () => {
          invalidateSecrets();
          displaySuccessToast(t(I18nKey.SETTINGS$GPG_KEY_SAVED));
          onSaved();
        },
        onError: (err) => {
          const message = retrieveAxiosErrorMessage(err);
          displayErrorToast(message || t(I18nKey.ERROR$GENERIC));
        },
      },
    );
  };

  return (
    <ModalBackdrop onClose={onClose}>
      <form
        data-testid="gpg-key-modal"
        onSubmit={handleSubmit}
        className="flex flex-col gap-2"
      >
        <ModalBody className="border border-tertiary" width="medium">
          <div className="flex flex-col gap-2 self-start w-full">
            <BaseModalTitle title={t(I18nKey.SETTINGS$GPG_KEY)} />
            <BaseModalDescription>
              {t(I18nKey.SETTINGS$GPG_KEY_MODAL_DESCRIPTION)}
            </BaseModalDescription>
          </div>

          <label className="flex flex-col gap-2.5 w-full">
            <span className="text-sm">
              {t(I18nKey.SETTINGS$GPG_KEY_VALUE_LABEL)}
            </span>
            <textarea
              data-testid="gpg-key-value"
              name="gpg-key-value"
              required
              className={cn(
                "resize-none",
                "bg-tertiary border border-[#717888] rounded-sm p-2 placeholder:italic placeholder:text-tertiary-alt",
                "disabled:bg-[#2D2F36] disabled:border-[#2D2F36] disabled:cursor-not-allowed",
              )}
              rows={12}
              placeholder={t(I18nKey.SETTINGS$GPG_KEY_VALUE_PLACEHOLDER)}
            />
          </label>

          {existingSecret && (
            <p className="text-xs text-tertiary-alt self-start">
              {t(I18nKey.SETTINGS$GPG_KEY_EXISTS)}
            </p>
          )}
          {error && <p className="text-red-500 text-sm self-start">{error}</p>}

          <div className="flex items-center gap-4 w-full justify-end">
            <BrandButton
              testId="gpg-key-cancel-button"
              type="button"
              variant="secondary"
              onClick={onClose}
            >
              {t(I18nKey.BUTTON$CANCEL)}
            </BrandButton>
            <BrandButton
              testId="gpg-key-save-button"
              type="submit"
              variant="primary"
              isDisabled={isPending}
            >
              {isPending
                ? t(I18nKey.SETTINGS$GPG_KEY_SAVING)
                : t(I18nKey.SETTINGS$GPG_KEY_SAVE)}
            </BrandButton>
          </div>
        </ModalBody>
      </form>
    </ModalBackdrop>
  );
}
