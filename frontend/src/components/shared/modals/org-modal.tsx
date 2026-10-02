import React from "react";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ModalBackdrop } from "./modal-backdrop";
import { ModalBody } from "./modal-body";
import { ModalButtonGroup } from "./modal-button-group";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";

interface OrgModalProps {
  testId?: string;
  title: string;
  description?: React.ReactNode;
  children?: React.ReactNode;
  primaryButtonText: string;
  secondaryButtonText?: string;
  onPrimaryClick?: () => void;
  onClose: () => void;
  isLoading?: boolean;
  primaryButtonType?: "button" | "submit";
  primaryButtonTestId?: string;
  secondaryButtonTestId?: string;
  secondaryButtonClassName?: string;
  ariaLabel?: string;
  asForm?: boolean;
  formAction?: (formData: FormData) => void;
  hideSecondaryButton?: boolean;
  isPrimaryDisabled?: boolean;
  showCloseButton?: boolean;
  hideTitle?: boolean;
  hideButtonGroup?: boolean;
  className?: string;
}

export function OrgModal({
  testId,
  title,
  description,
  children,
  primaryButtonText,
  secondaryButtonText,
  onPrimaryClick,
  onClose,
  isLoading = false,
  primaryButtonType = "button",
  primaryButtonTestId,
  secondaryButtonTestId,
  secondaryButtonClassName,
  ariaLabel,
  asForm = false,
  formAction,
  hideSecondaryButton = false,
  isPrimaryDisabled = false,
  showCloseButton = false,
  hideTitle = false,
  hideButtonGroup = false,
  className,
}: OrgModalProps) {
  const { t } = useTranslation();
  const content = (
    <>
      <div className="flex w-full flex-col gap-2">
        {hideTitle ? null : (
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 flex-col gap-2">
              <h3 className="text-xl font-bold">{title}</h3>
              {description ? (
                <p className="text-xs text-modal-muted">{description}</p>
              ) : null}
            </div>
            {showCloseButton ? (
              <button
                type="button"
                data-testid={testId ? `${testId}-close` : undefined}
                aria-label={t(I18nKey.BUTTON$CLOSE)}
                disabled={isLoading}
                onClick={onClose}
                className="flex shrink-0 cursor-pointer items-center justify-center rounded-sm border-0 bg-transparent p-1 text-tertiary-alt transition-colors hover:bg-surface-raised hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                <X className="size-4" aria-hidden />
              </button>
            ) : null}
          </div>
        )}
        {children}
      </div>
      {hideButtonGroup ? null : (
      <ModalButtonGroup
        primaryText={primaryButtonText}
        secondaryText={secondaryButtonText}
        onPrimaryClick={onPrimaryClick}
        onSecondaryClick={onClose}
        isLoading={isLoading}
        primaryType={primaryButtonType}
        primaryTestId={primaryButtonTestId}
        secondaryTestId={secondaryButtonTestId}
        secondaryClassName={secondaryButtonClassName}
        hideSecondaryButton={hideSecondaryButton}
        isPrimaryDisabled={isPrimaryDisabled}
      />
      )}
    </>
  );

  const modalBodyClassName = cn(
    "items-start rounded-xl p-6 w-sm flex flex-col gap-4 bg-base-secondary border border-tertiary",
    className,
  );

  return (
    <ModalBackdrop
      onClose={isLoading ? undefined : onClose}
      aria-label={ariaLabel}
    >
      {asForm ? (
        <form
          data-testid={testId}
          action={formAction}
          noValidate
          className={modalBodyClassName}
        >
          {content}
        </form>
      ) : (
        <ModalBody testID={testId} className={modalBodyClassName}>
          {content}
        </ModalBody>
      )}
    </ModalBackdrop>
  );
}
