import React from "react";
import { useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";

const ORG_CONTACT_FORM_URL = "https://www.all-hands.dev/contact";
const CAROUSEL_INTERVAL_MS = 3500;

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
  const [activeSlide, setActiveSlide] = React.useState(0);
  const hasMultipleSlides = screenshotPlaceholders.length > 1;

  React.useEffect(() => {
    if (!hasMultipleSlides) return undefined;

    const interval = window.setInterval(() => {
      setActiveSlide(
        (currentSlide) => (currentSlide + 1) % screenshotPlaceholders.length,
      );
    }, CAROUSEL_INTERVAL_MS);

    return () => window.clearInterval(interval);
  }, [hasMultipleSlides]);

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
      className="w-[92vw] max-w-[640px]"
    >
      <div className="w-full overflow-hidden rounded-xl border border-[#717888] bg-[#17181B]">
        <div
          className="flex transition-transform duration-700 ease-out"
          style={{ transform: `translateX(-${activeSlide * 100}%)` }}
        >
          {screenshotPlaceholders.map((labelKey) => (
            <div key={labelKey} className="min-w-full p-4">
              <div className="h-72 rounded-lg bg-gradient-to-br from-[#34373D] via-[#26282D] to-[#111214] p-4 flex flex-col overflow-hidden shadow-inner">
                <div className="flex items-center justify-between border-b border-white/10 pb-3">
                  <div className="flex gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-full bg-[#FF5F57]" />
                    <span className="h-2.5 w-2.5 rounded-full bg-[#FFBD2E]" />
                    <span className="h-2.5 w-2.5 rounded-full bg-[#28C840]" />
                  </div>
                  <div className="h-2 w-28 rounded bg-white/20" />
                </div>

                <div className="grid flex-1 grid-cols-[1fr_1.35fr] gap-4 py-5">
                  <div className="space-y-3">
                    <div className="h-4 w-3/4 rounded bg-white/35" />
                    <div className="h-3 w-full rounded bg-white/15" />
                    <div className="h-3 w-5/6 rounded bg-white/15" />
                    <div className="mt-5 space-y-2 rounded-lg border border-white/10 bg-white/5 p-3">
                      <div className="h-2.5 w-full rounded bg-white/20" />
                      <div className="h-2.5 w-2/3 rounded bg-white/15" />
                    </div>
                  </div>

                  <div className="rounded-lg border border-white/10 bg-black/20 p-3">
                    <div className="mb-3 h-3 w-1/2 rounded bg-white/25" />
                    <div className="grid h-[calc(100%-1.5rem)] grid-cols-2 gap-2">
                      <div className="rounded bg-[#D6C676]/80" />
                      <div className="rounded bg-white/20" />
                      <div className="rounded bg-white/15" />
                      <div className="rounded bg-[#D6C676]/50" />
                    </div>
                  </div>
                </div>

                <span className="text-sm font-semibold text-white">
                  {t(labelKey)}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {hasMultipleSlides && (
        <div className="flex w-full justify-center gap-2" aria-hidden="true">
          {screenshotPlaceholders.map((labelKey, index) => (
            <span
              key={labelKey}
              className={cn(
                "h-2 rounded-full transition-all duration-300",
                activeSlide === index ? "w-6 bg-[#D6C676]" : "w-2 bg-white/30",
              )}
            />
          ))}
        </div>
      )}
    </OrgModal>
  );
}
