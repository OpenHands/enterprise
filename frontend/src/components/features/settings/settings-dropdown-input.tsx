import { ComboBox, Input, ListBox, Spinner } from "@heroui/react";
import React, { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { OptionalTag } from "./optional-tag";
import { cn } from "#/utils/utils";

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
    <label className={cn("flex flex-col gap-2.5", wrapperClassName)}>
      {label && (
        <div className="flex items-center gap-1">
          <span className="text-sm">{label}</span>
          {showOptionalTag && <OptionalTag />}
        </div>
      )}
      <ComboBox
        aria-label={typeof label === "string" ? label : name}
        items={items}
        defaultSelectedKey={defaultSelectedKey}
        selectedKey={selectedKey ?? undefined}
        onSelectionChange={onSelectionChange}
        onInputChange={onInputChange}
        isDisabled={isDisabled || isLoading}
        allowsCustomValue={allowsCustomValue || isClearable}
        isRequired={required}
        defaultFilter={defaultFilter ?? (() => true)}
        className="w-full"
      >
        <ComboBox.InputGroup
          className={cn(
            "bg-tertiary border border-[#717888] h-10 w-full max-w-[680px] rounded-sm p-2",
            inputWrapperClassName,
          )}
        >
          {startContent || null}
          <Input
            data-testid={testId}
            name={name}
            placeholder={isLoading ? t("HOME$LOADING") : placeholder}
            className={cn("placeholder:italic", inputClassName)}
            onMouseDown={(e) => {
              if (document.activeElement === e.currentTarget) {
                e.currentTarget.blur();
              }
            }}
          />
          {isLoading ? <Spinner size="sm" /> : null}
        </ComboBox.InputGroup>
        <ComboBox.Popover className="bg-tertiary rounded-xl">
          <ListBox>
            {items.map((item) => (
              <ListBox.Item
                id={String(item.key)}
                key={item.key}
                textValue={item.label}
              >
                {item.label}
              </ListBox.Item>
            ))}
          </ListBox>
        </ComboBox.Popover>
      </ComboBox>
    </label>
  );
}
