import { cn } from "#/utils/utils";
import { ToggleSwitchVisual } from "#/ui/toggle-switch";

interface ToggleProps {
  checked: boolean;
  onClick?: () => void;
  disabled?: boolean;
  "aria-label"?: string;
  title?: string;
}

/** Settings-table toggle that shares the agent-canvas switch visual. */
export function Toggle({
  checked,
  onClick,
  disabled,
  "aria-label": ariaLabel,
  title,
}: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      title={title}
      disabled={disabled}
      onClick={disabled ? undefined : onClick}
      className={cn(
        "cursor-pointer",
        disabled && "cursor-not-allowed opacity-50",
      )}
    >
      <ToggleSwitchVisual enabled={checked} />
    </button>
  );
}
