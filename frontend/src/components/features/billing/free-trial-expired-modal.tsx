import { useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { ModalBackdrop } from "#/components/shared/modals/modal-backdrop";
import { ModalBody } from "#/components/shared/modals/modal-body";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { cn } from "#/utils/utils";

const PRICING_URL = "https://openhands.dev/pricing";
const CONTACT_SALES_PATH = "/information-request";

export function FreeTrialExpiredModal() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [showLicenseKey, setShowLicenseKey] = useState(false);
  const [licenseKey, setLicenseKey] = useState("");

  return (
    <ModalBackdrop>
      <ModalBody
        testID="free-trial-expired-modal"
        className="w-[480px] max-w-[92vw] items-start border border-tertiary"
      >
        <div className="flex w-full flex-col gap-2">
          <h3 className="text-xl font-bold text-white">
            {t(I18nKey.FREE_TRIAL$EXPIRED_TITLE)}
          </h3>
          <p className="text-sm leading-5 text-modal-muted">
            {t(I18nKey.FREE_TRIAL$EXPIRED_DESCRIPTION)}
          </p>
        </div>
        <div className="flex w-full flex-row gap-2">
          <BrandButton
            type="button"
            variant="primary"
            testId="free-trial-expired-choose-plan"
            className="min-w-0 flex-1"
            onClick={() =>
              window.open(PRICING_URL, "_blank", "noopener,noreferrer")
            }
          >
            {t(I18nKey.FREE_TRIAL$CHOOSE_PLAN)}
          </BrandButton>
          <BrandButton
            type="button"
            variant="secondary"
            testId="free-trial-expired-contact-sales"
            className="min-w-0 flex-1"
            onClick={() => navigate(CONTACT_SALES_PATH)}
          >
            {t(I18nKey.FREE_TRIAL$CONTACT_SALES)}
          </BrandButton>
        </div>
        <div className="flex w-full flex-col items-center gap-3">
          {!showLicenseKey ? (
            <button
              type="button"
              data-testid="free-trial-expired-enter-key"
              className={cn(
                "text-sm font-medium text-white underline-offset-2",
                "hover:text-white/80 hover:underline",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white",
              )}
              onClick={() => setShowLicenseKey(true)}
            >
              {t(I18nKey.FREE_TRIAL$ENTER_LICENSE_KEY)}
            </button>
          ) : (
            <div
              className="flex w-full flex-col gap-2"
              data-testid="free-trial-expired-license-form"
            >
              <SettingsInput
                label={t(I18nKey.FREE_TRIAL$LICENSE_KEY_LABEL)}
                type="text"
                value={licenseKey}
                placeholder={t(I18nKey.FREE_TRIAL$LICENSE_KEY_PLACEHOLDER)}
                onChange={setLicenseKey}
              />
              <BrandButton
                type="button"
                variant="secondary"
                testId="free-trial-expired-apply-key"
                className="w-full"
                isDisabled={!licenseKey.trim()}
                onClick={() => undefined}
              >
                {t(I18nKey.FREE_TRIAL$APPLY_LICENSE_KEY)}
              </BrandButton>
            </div>
          )}
        </div>
      </ModalBody>
    </ModalBackdrop>
  );
}
