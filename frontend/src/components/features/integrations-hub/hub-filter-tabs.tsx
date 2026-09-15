import { cn } from "#/utils/utils";
import { formControlTransitionClassName } from "#/utils/form-control-classes";
import { HubCountBadge } from "./hub-count-badge";

interface HubFilterTabOption<T extends string> {
  value: T;
  label: string;
  count?: number;
}

interface HubFilterTabsProps<T extends string> {
  value: T;
  options: HubFilterTabOption<T>[];
  onChange: (value: T) => void;
  ariaLabel: string;
  testId?: string;
}

export function HubFilterTabs<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  testId,
}: HubFilterTabsProps<T>) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      data-testid={testId}
      className="flex items-center gap-2"
    >
      {options.map((option) => {
        const isActive = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={isActive}
            data-testid={testId ? `${testId}-${option.value}` : undefined}
            className={cn(
              "inline-flex w-fit cursor-pointer items-center rounded-none bg-transparent px-2 py-2 text-sm capitalize",
              formControlTransitionClassName,
              "border-b-2 pb-2",
              isActive
                ? "border-white text-white"
                : "border-transparent text-[var(--oh-muted)] hover:text-white",
            )}
            onClick={() => onChange(option.value)}
          >
            {option.label}
            {option.count !== undefined ? (
              <HubCountBadge count={option.count} className="ml-2" />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
