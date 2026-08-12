/* eslint-disable react/jsx-props-no-spreading */
import { cn } from "#/utils/utils";
import { formControlInlineInputClassName } from "#/utils/form-control-classes";

interface DropdownInputProps {
  placeholder?: string;
  isDisabled: boolean;
  getInputProps: (props?: object) => object;
  /** When false, the field is a select trigger rather than a typeahead. */
  searchable?: boolean;
}

export function DropdownInput({
  placeholder,
  isDisabled,
  getInputProps,
  searchable = true,
}: DropdownInputProps) {
  return (
    <input
      {...getInputProps({
        placeholder,
        disabled: isDisabled,
        readOnly: !searchable,
        className: cn(
          "outline-none bg-transparent text-white not-italic",
          "flex-1 min-w-0",
          "placeholder:text-tertiary-alt",
          formControlInlineInputClassName,
          "px-0 not-italic text-inherit",
          !searchable && "cursor-pointer caret-transparent",
        ),
      })}
    />
  );
}
