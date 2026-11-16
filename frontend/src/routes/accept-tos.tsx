import React from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { I18nKey } from "#/i18n/declaration";
import OpenHandsLogoWhite from "#/assets/branding/openhands-logo-white.svg?react";
import { TOSCheckbox } from "#/components/features/waitlist/tos-checkbox";
import { BrandButton } from "#/components/features/settings/brand-button";
import { ModalBackdrop } from "#/components/shared/modals/modal-backdrop";
import {
  ENTERPRISE_TERMS_OF_SERVICE_URL,
  TERMS_OF_SERVICE_URL,
} from "#/constants/legal-links";
import { useAcceptTos } from "#/hooks/mutation/use-accept-tos";
import { useAppMode } from "#/hooks/use-app-mode";

const CLOUD_PRODUCT_NAME = "OpenHands Cloud";
const ENTERPRISE_PRODUCT_NAME = "OpenHands Enterprise";

export default function AcceptTOS() {
  const { t } = useTranslation();
  const [searchParams] = useSearchParams();
  const [isTosAccepted, setIsTosAccepted] = React.useState(false);
  const { isEnterpriseCloud, isEnterpriseSelfHosted } = useAppMode();

  // Get the redirect URL from the query parameters
  const redirectUrl = searchParams.get("redirect_url") || "/";

  // Use mutation for accepting TOS
  const { mutate: acceptTOS, isPending: isSubmitting } = useAcceptTos();

  // Name the product the terms cover. Until the web-client config is in the
  // cache (or on a backend that reports no deployment_mode) keep the generic
  // wording rather than guess a product.
  let product: string | null = null;
  if (isEnterpriseSelfHosted) {
    product = ENTERPRISE_PRODUCT_NAME;
  } else if (isEnterpriseCloud) {
    product = CLOUD_PRODUCT_NAME;
  }
  const termsUrl = isEnterpriseSelfHosted
    ? ENTERPRISE_TERMS_OF_SERVICE_URL
    : TERMS_OF_SERVICE_URL;

  const handleAcceptTOS = () => {
    if (isTosAccepted && !isSubmitting) {
      acceptTOS({ redirectUrl });
    }
  };

  return (
    <ModalBackdrop>
      <div className="border border-[var(--oh-border)] p-8 rounded-lg max-w-md w-full flex flex-col gap-6 items-center bg-base-secondary">
        <OpenHandsLogoWhite width={56} height={56} />

        <div className="flex flex-col gap-2 w-full items-center text-center">
          <h1 className="text-2xl font-bold">
            {product
              ? t(I18nKey.TOS$ACCEPT_PRODUCT_TERMS_OF_SERVICE, { product })
              : t(I18nKey.TOS$ACCEPT_TERMS_OF_SERVICE)}
          </h1>
          <p className="text-sm text-gray-500">
            {product
              ? t(I18nKey.TOS$ACCEPT_PRODUCT_TERMS_DESCRIPTION, { product })
              : t(I18nKey.TOS$ACCEPT_TERMS_DESCRIPTION)}
          </p>
        </div>

        <TOSCheckbox
          termsUrl={termsUrl}
          onChange={() => setIsTosAccepted((prev) => !prev)}
        />

        <BrandButton
          isDisabled={!isTosAccepted || isSubmitting}
          type="button"
          variant="primary"
          onClick={handleAcceptTOS}
          className="w-full font-semibold"
        >
          {isSubmitting ? t(I18nKey.HOME$LOADING) : t(I18nKey.TOS$CONTINUE)}
        </BrandButton>
      </div>
    </ModalBackdrop>
  );
}
