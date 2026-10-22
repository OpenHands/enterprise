import { forwardRef } from "react";
import { cn } from "#/utils/utils";
import { formControlMultilineFieldClassName } from "#/utils/form-control-classes";
import { OptionalTag } from "./optional-tag";

interface SettingsTextareaProps {
  testId?: string;
  name?: string;
  label: string;
  defaultValue?: string;
  value?: string;
  placeholder?: string;
  showOptionalTag?: boolean;
  description?: React.ReactNode;
  isDisabled?: boolean;
  /** Keeps the value visible/copyable while blocking edits (e.g. a
   * generated cert/key the admin only needs to read or copy). */
  isReadOnly?: boolean;
  className?: string;
  onChange?: (value: string) => void;
  onBlur?: () => void;
  required?: boolean;
  labelClassName?: string;
  /** ARIA describedby attribute for accessibility */
  ariaDescribedBy?: string;
  /** ARIA invalid attribute for accessibility */
  ariaInvalid?: boolean;
  /**
   * Validation error message. When set, the textarea gets a red border and
   * the message is rendered below it. Also sets aria-invalid automatically.
   */
  error?: string;
  /** Renders a red asterisk next to the label to mark the field as required. */
  showRequiredTag?: boolean;
  /**
   * Short guidance rendered next to the label, above the textarea. It sits
   * inside the `<label>`, so it is announced as part of the field's
   * accessible name -- keep it to a phrase that reads well after the label
   * text.
   */
  hint?: string;
  /** Visible row count -- defaults to a size that comfortably fits a
   * PEM-encoded certificate/key without scrolling. */
  rows?: number;
  /** Extra classes merged onto the `<textarea>` element. */
  textareaClassName?: string;
}

export const SettingsTextarea = forwardRef<
  HTMLTextAreaElement,
  SettingsTextareaProps
>(
  (
    {
      testId,
      name,
      label,
      defaultValue,
      value,
      placeholder,
      showOptionalTag,
      description,
      isDisabled,
      isReadOnly,
      className,
      onChange,
      onBlur,
      required,
      labelClassName,
      ariaDescribedBy,
      ariaInvalid,
      error,
      showRequiredTag,
      hint,
      rows = 6,
      textareaClassName,
    },
    ref,
  ) => {
    const errorId = error && testId ? `${testId}-error` : undefined;
    return (
      <label className={cn("flex flex-col gap-2.5 w-full min-w-0", className)}>
        <div className="flex items-center gap-2">
          <span className={cn("text-sm", labelClassName)}>{label}</span>
          {showRequiredTag && (
            <span className="text-red-400 text-sm leading-none" aria-hidden>
              *
            </span>
          )}
          {showOptionalTag && <OptionalTag />}
          {hint && (
            <span
              data-testid={testId ? `${testId}-hint` : undefined}
              className="min-w-0 text-xs text-[var(--oh-muted)]"
            >
              {hint}
            </span>
          )}
        </div>
        {description}
        <textarea
          ref={ref}
          data-testid={testId}
          onChange={(e) => onChange?.(e.target.value)}
          onBlur={onBlur}
          name={name}
          disabled={isDisabled}
          readOnly={isReadOnly}
          defaultValue={defaultValue}
          value={value}
          placeholder={placeholder}
          required={required}
          rows={rows}
          aria-describedby={errorId ?? ariaDescribedBy}
          aria-invalid={!!error || ariaInvalid}
          className={cn(
            formControlMultilineFieldClassName,
            "resize-none font-mono text-xs",
            "disabled:bg-[var(--oh-surface-raised)] disabled:border-[var(--oh-border-subtle)]",
            isReadOnly &&
              "cursor-not-allowed opacity-70 bg-[var(--oh-surface-raised)] border-[var(--oh-border-subtle)]",
            error && "border-red-500",
            textareaClassName,
          )}
        />
        {error && (
          <p
            id={errorId}
            role="alert"
            data-testid={testId ? `${testId}-error` : undefined}
            className="text-xs text-red-400 -mt-1"
          >
            {error}
          </p>
        )}
      </label>
    );
  },
);
SettingsTextarea.displayName = "SettingsTextarea";
