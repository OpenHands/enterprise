import { cn } from "#/utils/utils";

/** Nav rows and side nav links — hover color/background snap instantly (no fade). */
export const navInteractiveTransitionClassName =
  "transition-none motion-reduce:transition-none";

/** Expanded sidebar icon column beside labels (matches 36px nav rows). */
export const SIDEBAR_ICON_SLOT_CLASS =
  "flex h-9 w-[18px] shrink-0 items-center justify-center [&_svg]:h-[18px] [&_svg]:w-[18px]";

export const SIDEBAR_ROW_INTERACTIVE_CLASS = {
  active: "bg-tertiary text-white font-normal",
  idle: "text-[var(--oh-muted)] hover:text-white hover:bg-[var(--oh-surface-raised)]",
} as const;

export function sidebarNavRowClassName(): string {
  return cn(
    "flex h-9 min-h-9 min-w-0 items-center rounded-md",
    navInteractiveTransitionClassName,
    "gap-2 px-2.5 overflow-hidden text-sm leading-5 w-full",
  );
}

export function sidebarNavLabelClassName(): string {
  return "min-w-0 truncate";
}

/** Trailing count pill used on settings / Hub nav rows. */
export const SIDEBAR_NAV_COUNT_CLASS =
  "inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-surface-raised px-1.5 text-[11px] font-medium leading-none tabular-nums text-foreground ml-auto";
