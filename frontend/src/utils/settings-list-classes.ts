import { cn } from "#/utils/utils";
import {
  formControlBorderClassName,
  formControlRadiusClassName,
  formControlSurfaceClassName,
  formControlTransitionClassName,
} from "#/utils/form-control-classes";

/** 48px row height — taller than form controls for settings list/table rows. */
export const settingsListRowHeightClassName = "h-12 min-h-12";

/** Bordered list shell shared by secrets, LLM profiles, and similar settings tables. */
export const settingsListContainerClassName = cn(
  formControlBorderClassName,
  formControlRadiusClassName,
  formControlSurfaceClassName,
  "overflow-hidden",
);

/** Scrollable variant for long settings lists (e.g. secrets). */
export const settingsListScrollContainerClassName = cn(
  settingsListContainerClassName,
  "overflow-auto max-h-[min(70vh,39rem)]",
);

export const settingsListDividerClassName =
  "divide-y divide-[var(--oh-border)]";

/** Interactive row hover on dark list surfaces (base-secondary). */
export const settingsListRowHoverClassName =
  "hover:bg-[var(--oh-interactive-hover-low)]";

export const settingsListRowClassName = cn(
  settingsListRowHeightClassName,
  "flex items-center px-3",
  formControlTransitionClassName,
  settingsListRowHoverClassName,
);

export const settingsListTableRowClassName = cn(
  settingsListRowHoverClassName,
  formControlTransitionClassName,
  "border-t border-[var(--oh-border)] first:border-t-0",
);

/** Compact in-list section title (not a column header). Do not use table-head/sticky classes. */
export const settingsListSectionHeaderClassName = cn(
  "flex h-9 w-full items-center justify-between border-b border-[var(--oh-border)] bg-base-secondary/50 px-3 text-[11px] font-medium leading-4 text-tertiary-alt",
);

export const settingsListTableHeadClassName = cn(
  "sticky top-0 z-10 border-b border-[var(--oh-border)] bg-base-secondary/50",
);

/** Compact muted column labels — distinct from body rows (h-12 / text-sm). */
export const settingsListTableHeaderCellClassName = cn(
  "h-9 min-h-9 px-4 text-left text-xs font-medium leading-4 text-tertiary-alt align-middle",
);

export const settingsListTableCellClassName = cn(
  settingsListRowHeightClassName,
  "px-3 text-sm align-middle min-w-0",
);

export const settingsListIconActionButtonClassName = cn(
  "inline-flex cursor-pointer items-center justify-center rounded-md p-1 text-muted",
  formControlTransitionClassName,
  "hover:bg-[var(--oh-interactive-hover-low)] hover:text-white",
);

/**
 * Compact primary CTA on integration provider rows (GitHub / Slack / Azure, etc.).
 * Matches agent-canvas compact action height (h-7 / text-xs).
 */
export const settingsListRowActionButtonClassName =
  "h-7 min-h-7 w-fit whitespace-nowrap px-2.5 text-xs";
