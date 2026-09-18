import React from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { BrandButton } from "#/components/features/settings/brand-button";
import { CreateApiKeyResponse } from "#/api/api-keys";
import { displaySuccessToast } from "#/utils/custom-toast-handlers";
import { ApiKeyModalBase } from "./api-key-modal-base";

interface NewApiKeyModalProps {
  isOpen: boolean;
  newlyCreatedKey: CreateApiKeyResponse | null;
  onClose: () => void;
}

export function NewApiKeyModal({
  isOpen,
  newlyCreatedKey,
  onClose,
}: NewApiKeyModalProps) {
  const { t } = useTranslation();

  const handleCopyToClipboard = () => {
    if (newlyCreatedKey) {
      navigator.clipboard.writeText(newlyCreatedKey.key);
      displaySuccessToast(t(I18nKey.SETTINGS$API_KEY_COPIED));
    }
  };

  if (!newlyCreatedKey) return null;

  const modalFooter = (
    <>
      <BrandButton type="button" variant="secondary" onClick={onClose}>
        {t(I18nKey.BUTTON$CLOSE)}
      </BrandButton>
      <BrandButton
        type="button"
        variant="primary"
        onClick={handleCopyToClipboard}
      >
        {t(I18nKey.BUTTON$COPY_TO_CLIPBOARD)}
      </BrandButton>
    </>
  );

  return (
    <ApiKeyModalBase
      isOpen={isOpen && !!newlyCreatedKey}
      title={t(I18nKey.SETTINGS$API_KEY_CREATED)}
      width="600px"
      footer={modalFooter}
    >
      <div data-testid="new-api-key-modal">
        <p className="text-xs leading-5 text-[var(--oh-muted)]">
          {t(I18nKey.SETTINGS$API_KEY_WARNING)}
        </p>
        <div className="mt-4 break-all rounded-lg border border-[var(--oh-border)] bg-base-secondary p-4 font-mono text-sm">
          {newlyCreatedKey.key}
        </div>
      </div>
    </ApiKeyModalBase>
  );
}
