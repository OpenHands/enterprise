import { cn } from "#/utils/utils";
import {
  formControlHeightClassName,
  formControlTransitionClassName,
} from "#/utils/form-control-classes";

interface CreditsChipProps {
  testId?: string;
  className?: string;
}

/**
 * Chip component for displaying credits amount.
 * Uses the logo accent token so it stays themeable under neo.
 */
export function CreditsChip({
  children,
  testId,
  className,
}: React.PropsWithChildren<CreditsChipProps>) {
  return (
    <div
      data-testid={testId}
      data-openhands-chip
      className={cn(
        formControlHeightClassName,
        formControlTransitionClassName,
        "inline-flex min-w-[100px] items-center justify-center rounded-lg bg-logo px-4 text-center text-sm font-semibold text-[var(--oh-color-base)]",
        className,
      )}
    >
      {children}
    </div>
  );
}
