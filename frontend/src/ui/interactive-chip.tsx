import { cn } from "#/utils/utils";
import { formControlButtonClassName } from "#/utils/form-control-classes";

interface InteractiveChipProps {
  onClick: () => void;
  testId?: string;
  className?: string;
}

/**
 * Small clickable chip for secondary actions like "Add".
 * Matches agent-canvas secondary brand-button chrome.
 */
export function InteractiveChip({
  children,
  onClick,
  testId,
  className,
}: React.PropsWithChildren<InteractiveChipProps>) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className={cn(
        formControlButtonClassName,
        "border border-[var(--oh-border)] bg-base-secondary text-white hover:bg-surface-raised",
        className,
      )}
    >
      {children}
    </button>
  );
}
