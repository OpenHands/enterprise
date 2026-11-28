import { useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { BrandButton } from "#/components/features/settings/brand-button";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";

interface ScimTokenModalProps {
  token: string;
  onClose: () => void;
}

/**
 * Shows a new SCIM token, the only time it is available. Not dismissible by
 * Escape or a backdrop click, so it cannot be lost to a stray key press.
 */
export function ScimTokenModal({ token, onClose }: ScimTokenModalProps) {
  const { t } = useTranslation();

  const copyToken = async () => {
    try {
      await navigator.clipboard.writeText(token);
      displaySuccessToast(t(I18nKey.SCIM$TOKEN_COPIED));
    } catch {
      displayErrorToast(t(I18nKey.SCIM$TOKEN_COPY_FAILED));
    }
  };

  return (
    <OrgModal
      testId="scim-token-modal"
      title={t(I18nKey.SCIM$TOKEN_TITLE)}
      ariaLabel={t(I18nKey.SCIM$TOKEN_TITLE)}
      description={t(I18nKey.SCIM$TOKEN_WARNING)}
      primaryButtonText={t(I18nKey.SCIM$TOKEN_DONE)}
      onPrimaryClick={onClose}
      onClose={onClose}
      hideSecondaryButton
      dismissible={false}
      primaryButtonTestId="scim-token-done-button"
      className="w-[calc(100vw-2rem)] max-w-lg"
    >
      <div className="flex w-full flex-col gap-3">
        <output
          data-testid="scim-token-value"
          aria-label={t(I18nKey.SCIM$TOKEN_LABEL)}
          className="block select-all break-all rounded-lg border border-[var(--oh-border)] bg-base p-3 font-mono text-sm"
        >
          {token}
        </output>
        <BrandButton
          testId="scim-token-copy-button"
          type="button"
          variant="secondary"
          className="self-start"
          onClick={copyToken}
        >
          {t(I18nKey.BUTTON$COPY_TO_CLIPBOARD)}
        </BrandButton>
      </div>
    </OrgModal>
  );
}
