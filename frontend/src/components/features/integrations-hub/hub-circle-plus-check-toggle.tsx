import { useState, type MouseEvent } from "react";
import { Check, Plus, X } from "lucide-react";
import { formControlTransitionClassName } from "#/utils/form-control-classes";
import { cn } from "#/utils/utils";

interface HubCirclePlusCheckToggleProps {
  testId?: string;
  isSelected: boolean;
  onToggle: (selected: boolean) => void;
  isDisabled?: boolean;
  className?: string;
  enableLabel: string;
  disableLabel: string;
  enableTooltip: string;
  disableTooltip?: string;
  removeTooltip: string;
}

export function HubCirclePlusCheckToggle({
  testId,
  isSelected,
  onToggle,
  isDisabled = false,
  className,
  enableLabel,
  disableLabel,
  enableTooltip,
  disableTooltip,
  removeTooltip,
}: HubCirclePlusCheckToggleProps) {
  const [isPointerOver, setIsPointerOver] = useState(false);
  const clearPointerOver = () => setIsPointerOver(false);

  const handleClick = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (isDisabled) {
      return;
    }
    onToggle(!isSelected);
    clearPointerOver();
    event.currentTarget.blur();
  };

  const showRemoveIcon = isSelected && isPointerOver;
  const selectedTooltip = disableTooltip ?? removeTooltip;
  const tooltipLabel = isSelected ? selectedTooltip : enableTooltip;
  const ariaLabel = isSelected ? disableLabel : enableLabel;
  let icon = <Plus className="size-3" aria-hidden />;
  if (showRemoveIcon) {
    icon = <X className="size-3.5 stroke-[2.5]" aria-hidden />;
  } else if (isSelected) {
    icon = <Check className="size-3.5" aria-hidden />;
  }

  return (
    <button
      type="button"
      role="switch"
      aria-checked={isSelected}
      data-testid={testId}
      data-showing-remove={showRemoveIcon ? "true" : "false"}
      disabled={isDisabled}
      aria-label={ariaLabel}
      title={tooltipLabel}
      onClick={handleClick}
      onPointerEnter={() => setIsPointerOver(true)}
      onPointerLeave={clearPointerOver}
      onPointerCancel={clearPointerOver}
      onBlur={clearPointerOver}
      className={cn(
        "inline-flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-full p-0",
        formControlTransitionClassName,
        isSelected &&
          (showRemoveIcon
            ? "border-0 bg-[rgba(248,113,113,0.14)] text-[#ef4444] hover:bg-[rgba(248,113,113,0.24)]"
            : "border border-white bg-transparent text-white"),
        !isSelected &&
          "border-0 bg-surface-raised text-white hover:bg-[var(--oh-interactive-hover)]",
        isDisabled && "cursor-not-allowed opacity-50",
        className,
      )}
    >
      {icon}
    </button>
  );
}
