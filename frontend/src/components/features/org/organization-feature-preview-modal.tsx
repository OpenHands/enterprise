import React from "react";
import { useTranslation } from "react-i18next";
import budgetsPreview from "#/assets/org-preview/budgets.png";
import usageMonitoringPreview from "#/assets/org-preview/usage-monitoring.png";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { useClientAnalytics } from "#/hooks/use-client-analytics";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";

const ENTERPRISE_QUICK_START_URL =
  "https://docs.openhands.dev/enterprise/quick-start";
const CAROUSEL_INTERVAL_MS = 3500;

const screenshotSlides = [
  {
    src: usageMonitoringPreview,
    labelKey: I18nKey.ORG$FEATURE_PREVIEW_USAGE,
  },
  {
    src: budgetsPreview,
    labelKey: I18nKey.SETTINGS$NAV_BUDGETS,
  },
];

interface OrganizationFeaturePreviewModalProps {
  onClose: () => void;
}

export function OrganizationFeaturePreviewModal({
  onClose,
}: OrganizationFeaturePreviewModalProps) {
  const { t } = useTranslation();
  const { trackEnterpriseCTAClicked } = useClientAnalytics();
  const [activeSlide, setActiveSlide] = React.useState(0);
  const hasMultipleSlides = screenshotSlides.length > 1;

  React.useEffect(() => {
    if (!hasMultipleSlides) return undefined;

    const interval = window.setInterval(() => {
      setActiveSlide(
        (currentSlide) => (currentSlide + 1) % screenshotSlides.length,
      );
    }, CAROUSEL_INTERVAL_MS);

    return () => window.clearInterval(interval);
  }, [hasMultipleSlides]);

  return (
    <OrgModal
      testId="organization-feature-preview-modal"
      title={t(I18nKey.ORG$FEATURE_PREVIEW_TITLE)}
      description={t(I18nKey.ORG$FEATURE_PREVIEW_DESCRIPTION)}
      primaryButtonText={t(I18nKey.ORG$TRY_IT_OUT)}
      onPrimaryClick={() => {
        trackEnterpriseCTAClicked({
          location: "organization_feature_preview_modal_try_it_out",
        });
        window.open(ENTERPRISE_QUICK_START_URL, "_blank", "noopener");
      }}
      onClose={onClose}
      ariaLabel={t(I18nKey.ORG$FEATURE_PREVIEW_TITLE)}
      className="w-[92vw] max-w-[900px]"
    >
      <div className="w-full overflow-hidden rounded-xl border border-[#717888] bg-[#0D0E10]">
        <div
          className="flex transition-transform duration-700 ease-out"
          style={{ transform: `translateX(-${activeSlide * 100}%)` }}
        >
          {screenshotSlides.map((slide) => (
            <div key={slide.labelKey} className="min-w-full p-3">
              <div className="relative h-[420px] overflow-hidden rounded-lg bg-black shadow-inner">
                <img
                  src={slide.src}
                  alt={t(slide.labelKey)}
                  className="h-full w-full object-contain"
                />
                <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-4">
                  <span className="text-sm font-semibold text-white">
                    {t(slide.labelKey)}
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {hasMultipleSlides && (
        <div className="flex w-full justify-center gap-2" aria-hidden="true">
          {screenshotSlides.map((slide, index) => (
            <span
              key={slide.labelKey}
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
