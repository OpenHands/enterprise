import { useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { I18nKey } from "#/i18n/declaration";
import {
  clearSuperAdminNuxStarterModal,
  readSuperAdminNux,
  subscribeSuperAdminNux,
} from "#/utils/org/super-admin-nux";

const STARTER_STEPS = [
  I18nKey.SA_NUX$STARTER_LLM,
  I18nKey.SA_NUX$STARTER_AUTOMATION,
  I18nKey.SA_NUX$STARTER_OTHERS,
] as const;

/**
 * First visit to org LLM settings after install. Explains the basic setup
 * for the organization that was just created.
 */
export function SuperAdminGroupSetupModal() {
  const { t } = useTranslation();
  const nux = useSyncExternalStore(
    subscribeSuperAdminNux,
    readSuperAdminNux,
    readSuperAdminNux,
  );

  if (!nux.starterModalPending) {
    return null;
  }

  const orgName = nux.org?.name?.trim() || t(I18nKey.SA_NUX$ORG_DEFAULT_NAME);

  return (
    <OrgModal
      testId="sa-nux-starter-modal"
      title={t(I18nKey.SA_NUX$STARTER_TITLE, { name: orgName })}
      description={t(I18nKey.SA_NUX$STARTER_BODY)}
      primaryButtonText={t(I18nKey.SA_NUX$STARTER_CONFIRM)}
      primaryButtonTestId="sa-nux-starter-confirm"
      hideSecondaryButton
      onPrimaryClick={clearSuperAdminNuxStarterModal}
      onClose={clearSuperAdminNuxStarterModal}
    >
      <ol className="flex flex-col gap-2" data-testid="sa-nux-starter-steps">
        {STARTER_STEPS.map((key, index) => (
          <li key={key} className="flex items-start gap-3 text-sm text-white">
            <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border border-[var(--oh-border)] text-xs text-[var(--oh-muted)]">
              {index + 1}
            </span>
            <span>{t(key)}</span>
          </li>
        ))}
      </ol>
    </OrgModal>
  );
}
