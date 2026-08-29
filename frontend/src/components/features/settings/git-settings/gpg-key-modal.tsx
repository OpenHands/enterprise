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
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);

  const existingSecret = secrets?.find(
    (secret) => secret.name === GPG_SECRET_NAME,
  );

  const invalidateSecrets = () => {
    queryClient.invalidateQueries({ queryKey: ["secrets-search"] });
    queryClient.invalidateQueries({ queryKey: ["secrets", organizationId] });
  };

  const handleSubmit = () => {
    const value = textareaRef.current?.value.trim();

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
      <div data-testid="gpg-key-modal" className="flex flex-col gap-2">
        <ModalBody className="border border-tertiary" width="medium">
          <div className="flex flex-col gap-2 self-start w-full">
            <BaseModalTitle title={t(I18nKey.SETTINGS$GPG_KEY)} />
            <BaseModalDescription>
              {t(I18nKey.SETTINGS$GPG_KEY_MODAL_DESCRIPTION)}
            </BaseModalDescription>
            <div className="flex flex-col gap-1.5 w-full">
              <span className="text-xs text-modal-muted">
                {t(I18nKey.SETTINGS$GPG_KEY_EXPORT_STEP_1)}
              </span>
              <code className="self-start break-all bg-tertiary border border-[#717888] rounded-sm p-2 text-xs font-mono w-full">
                {t(I18nKey.SETTINGS$GPG_KEY_LIST_COMMAND)}
              </code>
              <span className="text-xs text-modal-muted mt-1">
                {t(I18nKey.SETTINGS$GPG_KEY_EXPORT_STEP_2)}
              </span>
              <code className="self-start break-all bg-tertiary border border-[#717888] rounded-sm p-2 text-xs font-mono w-full">
                {t(I18nKey.SETTINGS$GPG_KEY_EXPORT_COMMAND)}
              </code>
            </div>
          </div>

          <label className="flex flex-col gap-2.5 w-full">
            <span className="text-sm">
              {t(I18nKey.SETTINGS$GPG_KEY_VALUE_LABEL)}
            </span>
            <textarea
              ref={textareaRef}
              data-testid="gpg-key-value"
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
              type="button"
              variant="primary"
              isDisabled={isPending}
              onClick={handleSubmit}
            >
              {isPending
                ? t(I18nKey.SETTINGS$GPG_KEY_SAVING)
                : t(I18nKey.SETTINGS$GPG_KEY_SAVE)}
            </BrandButton>
          </div>
        </ModalBody>
      </div>
    </ModalBackdrop>
  );
}
