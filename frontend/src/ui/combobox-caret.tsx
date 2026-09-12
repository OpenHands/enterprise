import { cn } from "#/utils/utils";
import { formControlTransformTransitionClassName } from "#/utils/form-control-classes";

export function ComboboxCaretIcon({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden
      fill="none"
      focusable="false"
      height="1em"
      role="presentation"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={1.5}
      viewBox="0 0 24 24"
      width="1em"
      className={className}
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

/** Matches HeroUI Autocomplete selectorButton styling. Symmetric padding so
 * open-state rotation never shifts the caret horizontally. */
export const comboboxCaretButtonClassName = cn(
  "inline-flex items-center justify-center shrink-0 rounded-none bg-transparent px-1 min-w-0 w-auto h-auto text-medium cursor-pointer outline-none",
  formControlTransformTransitionClassName,
);

/** HeroUI Autocomplete selectorButton slot — keep only chevron rotation animated. */
export const heroUiAutocompleteSelectorButtonClassName = cn(
  comboboxCaretButtonClassName,
  "!rounded-none !bg-transparent data-[hover=true]:!bg-transparent",
);

/** Inline caret for buttons where only the icon rotates, not the whole control. */
export function ComboboxCaretInline({
  isOpen,
  className,
}: {
  isOpen?: boolean;
  className?: string;
}) {
  return (
    <ComboboxCaretIcon
      className={cn(
        "shrink-0",
        formControlTransformTransitionClassName,
        isOpen && "rotate-180",
        className,
      )}
    />
  );
}
