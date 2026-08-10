import { useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { I18nKey } from "#/i18n/declaration";

const ORG_CONTACT_FORM_URL = "https://www.all-hands.dev/contact";
const screenshotPlaceholders = [
  I18nKey.ORG$FEATURE_PREVIEW_MEMBERS,
  I18nKey.ORG$FEATURE_PREVIEW_USAGE,
  I18nKey.ORG$FEATURE_PREVIEW_SETTINGS,
];

interface OrganizationFeaturePreviewModalProps {
  onClose: () => void;
}

export function OrganizationFeaturePreviewModal({
  onClose,
}: OrganizationFeaturePreviewModalProps) {
  const { t } = useTranslation();

  return (
    <OrgModal
      testId="organization-feature-preview-modal"
      title={t(I18nKey.ORG$FEATURE_PREVIEW_TITLE)}
      description={t(I18nKey.ORG$FEATURE_PREVIEW_DESCRIPTION)}
      primaryButtonText={t(I18nKey.ORG$CONTACT_US)}
      onPrimaryClick={() =>
        window.open(ORG_CONTACT_FORM_URL, "_blank", "noopener")
      }
      onClose={onClose}
      ariaLabel={t(I18nKey.ORG$FEATURE_PREVIEW_TITLE)}
    >
      <div className="grid grid-cols-1 gap-3 w-full">
        {screenshotPlaceholders.map((labelKey) => (
          <div
            key={labelKey}
            className="h-24 rounded-lg border border-[#717888] bg-gradient-to-br from-[#2E3035] to-[#17181B] p-3 flex flex-col justify-between overflow-hidden"
          >
            <div className="flex gap-1.5">
              <span className="h-2 w-2 rounded-full bg-[#FF5F57]" />
              <span className="h-2 w-2 rounded-full bg-[#FFBD2E]" />
              <span className="h-2 w-2 rounded-full bg-[#28C840]" />
            </div>
            <div className="space-y-2">
              <div className="h-2 w-2/3 rounded bg-white/30" />
              <div className="h-2 w-1/2 rounded bg-white/15" />
            </div>
            <span className="text-[11px] text-modal-muted">{t(labelKey)}</span>
          </div>
        ))}
      </div>
    </OrgModal>
  );
}
