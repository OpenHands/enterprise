import { cn } from "#/utils/utils";

export const hubCardSurfaceClassName = "rounded-xl bg-base-secondary";

export const hubCardInteractiveClassName =
  "hover:bg-[var(--oh-interactive-hover-low)]";

export const hubCardPillClassName =
  "inline-flex max-w-full shrink-0 items-center whitespace-nowrap rounded-full border border-[var(--oh-border)] bg-[rgba(255,255,255,0.04)] px-2 py-0.5 text-[11px] leading-4 text-tertiary-light";

export const hubCardGridClassName =
  "grid min-w-0 grid-cols-1 gap-3 @min-[600px]:grid-cols-2";

export const hubEmptyStateClassName =
  "rounded-xl border border-[var(--oh-border)] p-8 text-center";

export const hubSearchEmptyStateClassName =
  "rounded-xl border border-[var(--oh-border)] p-6 text-center";

export const hubStatusPillClassName = cn(
  "inline-flex items-center rounded-md px-2.5 py-1 text-[11px] font-medium tracking-[0.01em]",
  "border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)] text-foreground",
);
