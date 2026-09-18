import React from "react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { I18nKey } from "#/i18n/declaration";

interface ModalButtonGroupProps {
  primaryText: string;
  secondaryText?: string;
  onPrimaryClick?: () => void;
  onSecondaryClick: () => void;
  isLoading?: boolean;
  primaryType?: "button" | "submit";
  primaryTestId?: string;
  secondaryTestId?: string;
  // For single-action modals where the primary button already closes; avoids a
  // redundant second "Close" button next to it.
  hideSecondaryButton?: boolean;
}

export function ModalButtonGroup({
  primaryText,
  secondaryText,
  onPrimaryClick,
  onSecondaryClick,
  isLoading = false,
  primaryType = "button",
  primaryTestId,
  secondaryTestId,
  hideSecondaryButton = false,
}: ModalButtonGroupProps) {
  const { t } = useTranslation();
  const closeText = secondaryText ?? t(I18nKey.BUTTON$CLOSE);

  return (
    <div className="flex w-full justify-end gap-2">
      {!hideSecondaryButton && (
        <BrandButton
          type="button"
          variant="secondary"
          onClick={onSecondaryClick}
          testId={secondaryTestId}
          isDisabled={isLoading}
        >
          {closeText}
        </BrandButton>
      )}
      <BrandButton
        type={primaryType}
        variant="primary"
        onClick={onPrimaryClick}
        className="flex items-center justify-center"
        testId={primaryTestId}
        isDisabled={isLoading}
      >
        {isLoading ? (
          <LoadingSpinner
            size="small"
            className="w-5 h-5"
            outerClassName="w-5 h-5"
          />
        ) : (
          primaryText
        )}
      </BrandButton>
    </div>
  );
}
