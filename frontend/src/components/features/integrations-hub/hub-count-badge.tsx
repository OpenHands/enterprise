import { cn } from "#/utils/utils";

interface HubCountBadgeProps {
  count: number;
  label?: string;
  className?: string;
}

export function HubCountBadge({ count, label, className }: HubCountBadgeProps) {
  return (
    <span
      className={cn(
        "ml-2 inline-flex items-center justify-center rounded-full bg-surface-raised px-2 py-0.5 text-xs text-foreground",
        className,
      )}
    >
      {label ? `${count} ${label}` : count}
    </span>
  );
}
