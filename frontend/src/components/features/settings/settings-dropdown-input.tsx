import { ComboBox, Input, ListBox } from "@heroui/react";
import React, { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { OptionalTag } from "./optional-tag";
import { cn } from "#/utils/utils";
import { formControlSettingsFieldClassName } from "#/utils/form-control-classes";

interface SettingsDropdownInputProps {
  testId: string;
  name: string;
  items: { key: React.Key; label: string }[];
  label?: ReactNode;
  wrapperClassName?: string;
  placeholder?: string;
  showOptionalTag?: boolean;
  isDisabled?: boolean;
  isLoading?: boolean;
  defaultSelectedKey?: string;
  selectedKey?: string | null;
  isClearable?: boolean;
  allowsCustomValue?: boolean;
  formValue?: "text" | "key";
  required?: boolean;
  onSelectionChange?: (key: React.Key | null) => void;
  onInputChange?: (value: string) => void;
  defaultFilter?: (textValue: string, inputValue: string) => boolean;
  startContent?: ReactNode;
  inputWrapperClassName?: string;
  inputClassName?: string;
}

export function SettingsDropdownInput({
  testId,
  label,
  wrapperClassName,
  name,
  items,
  placeholder,
  showOptionalTag,
  isDisabled,
  isLoading,
  defaultSelectedKey,
  selectedKey,
  isClearable,
  allowsCustomValue,
  formValue,
  required,
  onSelectionChange,
  onInputChange,
  defaultFilter,
  startContent,
  inputWrapperClassName,
  inputClassName,
}: SettingsDropdownInputProps) {
  const { t } = useTranslation();

  return (
    <label
      className={cn("flex flex-col gap-2.5 w-full min-w-0", wrapperClassName)}
    >
      {label && (
        <div className="flex items-center gap-1">
          <span className="text-sm">{label}</span>
          {showOptionalTag && <OptionalTag />}
        </div>
      )}
      <ComboBox
        aria-label={typeof label === "string" ? label : name}
        name={name}
        items={items}
        defaultSelectedKey={defaultSelectedKey}
        selectedKey={selectedKey}
        onSelectionChange={onSelectionChange}
        onInputChange={onInputChange}
        isDisabled={isDisabled || isLoading}
        isRequired={required}
        allowsCustomValue={allowsCustomValue}
        formValue={formValue}
        defaultFilter={defaultFilter}
        className="w-full"
      >
        <ComboBox.InputGroup>
          {startContent}
          <Input
            data-testid={testId}
            placeholder={isLoading ? t("HOME$LOADING") : placeholder}
            className={cn(
              formControlSettingsFieldClassName,
              inputWrapperClassName,
              inputClassName,
            )}
          />
          {isClearable ? (
            <button
              type="button"
              aria-label="Clear"
              data-testid={`${testId}-clear`}
              className="shrink-0 cursor-pointer px-1 text-tertiary-alt hover:text-white"
              onClick={() => {
                onSelectionChange?.(null);
                onInputChange?.("");
              }}
            >
              &times;
            </button>
          ) : null}
          <ComboBox.Trigger />
        </ComboBox.InputGroup>
        <ComboBox.Popover>
          <ListBox>
            {(item: { key: React.Key; label: string }) => (
              <ListBox.Item
                key={item.key}
                id={item.key as string | number}
                textValue={item.label}
              >
                {item.label}
                <ListBox.ItemIndicator />
              </ListBox.Item>
            )}
          </ListBox>
        </ComboBox.Popover>
      </ComboBox>
    </label>
  );
}
