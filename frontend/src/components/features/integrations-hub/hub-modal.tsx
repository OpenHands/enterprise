import type { ReactNode } from "react";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ModalBackdrop } from "#/components/shared/modals/modal-backdrop";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";

export const hubModalMaxWidthClassName = "max-w-[90vw]";

export const hubModalWidthClassName = {
  md: "w-[520px]",
  lg: "w-[640px]",
  xl: "w-[720px]",
} as const;

export function hubModalShellClassName(
  width: keyof typeof hubModalWidthClassName = "lg",
) {
  return cn(
    "relative flex max-h-[90vh] flex-col gap-6 overflow-hidden rounded-2xl border border-white/10 bg-base-secondary shadow-2xl",
    hubModalWidthClassName[width],
    hubModalMaxWidthClassName,
  );
}

export const hubModalFooterClassName =
  "flex flex-wrap items-center justify-end gap-2 border-t border-[var(--oh-border)] px-7 py-4";

export const hubModalBodyClassName =
  "min-h-0 flex-1 overflow-y-auto px-7 pb-4 pt-7";

export function HubModalCloseButton({
  onClose,
  testId,
  disabled,
}: {
  onClose: () => void;
  testId?: string;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClose}
      disabled={disabled}
      aria-label={t(I18nKey.INTEGRATIONS_HUB$CLOSE)}
      className="absolute right-4 top-4 z-10 flex cursor-pointer items-center justify-center rounded-sm border-0 bg-transparent p-1 text-tertiary-alt transition-colors hover:bg-surface-raised hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
    >
      <X className="size-4" aria-hidden />
    </button>
  );
}

interface HubModalProps {
  ariaLabel: string;
  testId: string;
  width?: keyof typeof hubModalWidthClassName;
  className?: string;
  onClose: () => void;
  children: ReactNode;
}

export function HubModal({
  ariaLabel,
  testId,
  width = "lg",
  className,
  onClose,
  children,
}: HubModalProps) {
  return (
    <ModalBackdrop aria-label={ariaLabel} onClose={onClose}>
      <section
        data-testid={testId}
        className={cn(hubModalShellClassName(width), className)}
      >
        <HubModalCloseButton onClose={onClose} testId={`${testId}-close`} />
        {children}
      </section>
    </ModalBackdrop>
  );
}
