import { cn } from "#/utils/utils";
import { formControlShellClassName } from "#/utils/form-control-classes";

/** Snap hover colors instantly — no transition-colors delay on menus/dropdowns. */
export const dropdownInstantColorClassName = "transition-none";

/** 2px vertical gap between rows in a dropdown/context menu list. */
export const dropdownMenuListGapClassName = "gap-0.5";

/** Standard horizontal gap between a row icon and its label. */
export const dropdownMenuRowGapClassName = "gap-2";

/** Flex column shell for a dropdown menu item list. */
export const dropdownMenuListClassName = cn(
  "flex flex-col",
  dropdownMenuListGapClassName,
);

/** Combobox/select trigger shell with instant hover colors. */
export const dropdownTriggerShellClassName = cn(
  formControlShellClassName,
  dropdownInstantColorClassName,
  "group w-full gap-2 pl-3 pr-1 text-[var(--oh-muted)] hover:text-white",
);

/** Standard white-label menu row. */
export const dropdownMenuRowClassName = cn(
  "group flex w-full cursor-pointer items-center rounded px-2 py-2 text-left text-sm font-normal text-white",
  dropdownMenuRowGapClassName,
  "hover:bg-[var(--oh-interactive-hover)] disabled:cursor-not-allowed disabled:opacity-60",
  dropdownInstantColorClassName,
);
