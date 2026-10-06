import {
  CloseButton,
  ComboBox,
  Input,
  ListBox,
  ListBoxItem,
  Spinner,
} from "@heroui/react";
import React, { ReactNode, useRef } from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
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
  const ariaLabel = typeof label === "string" ? label : name;
  const inputRef = useRef<HTMLInputElement>(null);

  // v3 has no `isClearable`; callers that opt in get an explicit clear control.
  const showClear = Boolean(isClearable) && selectedKey != null;

  // The v3 ComboBox only opens on first focus or on the trigger button; v2
  // reopened on every click, so forward clicks to the ArrowDown gesture.
  const openList = () => {
    if (isDisabled || isLoading) return;
    inputRef.current?.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "ArrowDown",
        bubbles: true,
        cancelable: true,
      }),
    );
  };

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
        aria-label={ariaLabel}
        items={items}
        defaultSelectedKey={defaultSelectedKey}
        selectedKey={selectedKey}
        onSelectionChange={onSelectionChange}
        onInputChange={onInputChange}
        isDisabled={isDisabled || isLoading}
        allowsCustomValue={allowsCustomValue}
        isRequired={required}
        defaultFilter={defaultFilter}
        className="w-full"
      >
        <ComboBox.InputGroup
          className={cn(
            formControlSettingsFieldClassName,
            inputWrapperClassName,
          )}
        >
          {startContent}
          <Input
            ref={inputRef}
            aria-label={ariaLabel}
            name={name}
            data-testid={testId}
            onClick={openList}
            placeholder={isLoading ? t("HOME$LOADING") : placeholder}
            className={cn(
              "min-w-0 flex-1 border-0 bg-transparent px-0 text-sm text-white outline-none",
              inputClassName,
            )}
          />
          {isLoading && <Spinner size="sm" />}
          {showClear && (
            <CloseButton
              aria-label={t(I18nKey.BUTTON$CLOSE)}
              onPress={() => onSelectionChange?.(null)}
            />
          )}
          <ComboBox.Trigger />
        </ComboBox.InputGroup>
        <ComboBox.Popover className="bg-content1 rounded-xl">
          <ListBox items={items}>
            {(item: { key: React.Key; label: string }) => (
              <ListBoxItem
                id={item.key as string | number}
                textValue={item.label}
              >
                {item.label}
                <ListBoxItem.Indicator />
              </ListBoxItem>
            )}
          </ListBox>
        </ComboBox.Popover>
      </ComboBox>
    </label>
  );
}
