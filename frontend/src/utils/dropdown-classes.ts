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
  // pl-0: left inset lives on the nested input so the full field surface
  // (including the leading padding) stays part of the combobox hit target.
  "group w-full gap-2 pl-0 pr-2.5 text-[var(--oh-muted)] hover:text-white",
);

/** Standard white-label menu row. */
export const dropdownMenuRowClassName = cn(
  "group flex w-full cursor-pointer items-center rounded px-2 py-2 text-left text-sm font-normal text-white",
  dropdownMenuRowGapClassName,
  "hover:bg-[var(--oh-interactive-hover)] disabled:cursor-not-allowed disabled:opacity-60",
  dropdownInstantColorClassName,
);

/** Menu row using foreground token (context menus). */
export const dropdownMenuRowForegroundClassName = cn(
  "group flex w-full cursor-pointer items-center rounded px-2 py-2 text-start text-sm font-normal",
  dropdownMenuRowGapClassName,
  "text-[var(--oh-foreground)] hover:bg-[var(--oh-interactive-hover)]",
  "disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent",
  dropdownInstantColorClassName,
);

/** Icon inside a menu row — muted until row hover/focus. */
export const dropdownMenuRowIconClassName = cn(
  "shrink-0 text-[var(--oh-muted)] group-hover:text-white group-focus-visible:text-white",
  dropdownInstantColorClassName,
);

/** Wrapper for SVG/React icon nodes inside a menu row. */
export const dropdownMenuRowIconWrapperClassName = cn(
  "flex size-4 shrink-0 items-center justify-center [&_svg]:text-current",
  dropdownMenuRowIconClassName,
);

/** Outer shell padding for dropdown / context-menu panels. */
export const dropdownMenuPanelPaddingClassName = "p-1";
