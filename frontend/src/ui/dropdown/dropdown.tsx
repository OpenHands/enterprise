import React, { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useCombobox } from "downshift";
import { cn } from "#/utils/utils";
import { DropdownOption } from "./types";
import { dropdownTriggerShellClassName } from "#/utils/dropdown-classes";
import { LoadingSpinner } from "./loading-spinner";
import { ClearButton } from "./clear-button";
import { ToggleButton } from "./toggle-button";
import { DropdownMenu } from "./dropdown-menu";
import { DropdownInput } from "./dropdown-input";

interface DropdownProps {
  options: DropdownOption[];
  emptyMessage?: string;
  clearable?: boolean;
  loading?: boolean;
  disabled?: boolean;
  placeholder?: string;
  defaultValue?: DropdownOption;
  onChange?: (item: DropdownOption | null) => void;
  testId?: string;
  className?: string;
  /** Extra classes for the nested input, such as a smaller type size. */
  inputClassName?: string;
  /** Draw a check beside the selected option. */
  showSelectionCheck?: boolean;
  /** Keep the open menu at least this wide when the trigger is narrower. */
  menuMinWidth?: number;
  /** When false, the trigger is a select (no typeahead filter). */
  searchable?: boolean;
  /** Icon or mark shown inside the closed trigger, before the value. */
  startContent?: React.ReactNode;
  /** Action row pinned above the option list. */
  header?: React.ReactNode;
  /** Action row pinned under the option list. */
  footer?: React.ReactNode;
}

export function Dropdown({
  options,
  emptyMessage = "No options",
  clearable = false,
  loading = false,
  disabled = false,
  placeholder,
  defaultValue,
  onChange,
  testId,
  className,
  inputClassName,
  showSelectionCheck = false,
  menuMinWidth,
  searchable = true,
  startContent,
  header,
  footer,
}: DropdownProps) {
  const [inputValue, setInputValue] = useState(defaultValue?.label ?? "");
  const [searchTerm, setSearchTerm] = useState("");
  const anchorRef = useRef<HTMLDivElement>(null);
  const [menuStyle, setMenuStyle] = useState<React.CSSProperties>();

  const filteredOptions = searchable
    ? options.filter((option) =>
        option.label.toLowerCase().includes(searchTerm.toLowerCase()),
      )
    : options;

  const {
    isOpen,
    selectedItem,
    selectItem,
    toggleMenu,
    getToggleButtonProps,
    getMenuProps,
    getItemProps,
    getInputProps,
    closeMenu,
  } = useCombobox({
    items: filteredOptions,
    itemToString: (item) => item?.label ?? "",
    inputValue,
    // Searchable: keep open on input click so users can reposition the caret.
    // Non-searchable: toggle like a select (click open / click close).
    stateReducer: (state, actionAndChanges) =>
      searchable &&
      actionAndChanges.type === useCombobox.stateChangeTypes.InputClick &&
      state.isOpen
        ? { ...actionAndChanges.changes, isOpen: true }
        : actionAndChanges.changes,
    onInputValueChange: ({ inputValue: newValue }) => {
      if (!searchable) {
        return;
      }
      setInputValue(newValue ?? "");
      setSearchTerm(newValue ?? "");
    },
    defaultSelectedItem: defaultValue,
    onSelectedItemChange: ({ selectedItem: newSelectedItem }) => {
      onChange?.(newSelectedItem ?? null);
    },
    onIsOpenChange: ({
      isOpen: newIsOpen,
      selectedItem: currentSelectedItem,
    }) => {
      if (newIsOpen) {
        if (searchable) {
          setSearchTerm("");
        }
      } else {
        setInputValue(currentSelectedItem?.label ?? "");
        setSearchTerm("");
      }
    },
  });

  const isDisabled = loading || disabled;

  useLayoutEffect(() => {
    if (!isOpen) {
      return undefined;
    }

    const updateMenuPosition = () => {
      const rect = anchorRef.current?.getBoundingClientRect();
      if (!rect) {
        return;
      }
      const gap = 4;
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;
      const openUp = spaceBelow < 220 && spaceAbove > spaceBelow;
      const width = Math.max(rect.width, menuMinWidth ?? 0);
      setMenuStyle({
        zIndex: 9999,
        left: rect.right - width,
        width,
        top: openUp ? undefined : rect.bottom + gap,
        bottom: openUp ? window.innerHeight - rect.top + gap : undefined,
      });
    };

    updateMenuPosition();
    window.addEventListener("resize", updateMenuPosition);
    window.addEventListener("scroll", updateMenuPosition, true);
    return () => {
      window.removeEventListener("resize", updateMenuPosition);
      window.removeEventListener("scroll", updateMenuPosition, true);
    };
  }, [isOpen, menuMinWidth]);

  // Wrap getInputProps to inject a direct onChange handler that preserves
  // cursor position. Downshift's default onInputValueChange resets cursor
  // to end of input on every keystroke; reading from e.target.value keeps
  // the browser's native cursor position intact.
  const getInputPropsWithCursorFix = (props?: object) =>
    getInputProps({
      ...props,
      onChange: searchable
        ? (e: React.ChangeEvent<HTMLInputElement>) => {
            setInputValue(e.target.value);
            setSearchTerm(e.target.value);
          }
        : undefined,
    });

  // Padding/gap on the bordered shell is not covered by the input or caret.
  // Treat those chrome clicks as a toggle so the whole control surface opens.
  const handleShellClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (isDisabled) {
      return;
    }
    const target = event.target as HTMLElement;
    if (target.closest("input, button")) {
      return;
    }
    const input = event.currentTarget.querySelector<HTMLInputElement>(
      'input[role="combobox"]',
    );
    input?.focus();
    toggleMenu();
  };

  return (
    <div
      ref={anchorRef}
      className={cn("relative w-full", isOpen && "z-50")}
      data-testid={testId}
    >
      <div
        className={cn(
          dropdownTriggerShellClassName,
          isDisabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
          className,
        )}
        onClick={handleShellClick}
      >
        {startContent}
        <DropdownInput
          placeholder={placeholder}
          isDisabled={isDisabled}
          getInputProps={getInputPropsWithCursorFix}
          searchable={searchable}
          className={inputClassName}
        />
        {loading && <LoadingSpinner />}
        {clearable && selectedItem && (
          <ClearButton onClear={() => selectItem(null)} />
        )}
        <ToggleButton
          isOpen={isOpen}
          isDisabled={isDisabled}
          getToggleButtonProps={getToggleButtonProps}
        />
      </div>
      {createPortal(
        <DropdownMenu
          isOpen={isOpen}
          filteredOptions={filteredOptions}
          selectedItem={selectedItem}
          emptyMessage={emptyMessage}
          getMenuProps={getMenuProps}
          getItemProps={getItemProps}
          header={header}
          footer={footer}
          onHeaderClick={closeMenu}
          onFooterClick={closeMenu}
          style={isOpen ? menuStyle : undefined}
          showSelectionCheck={showSelectionCheck}
        />,
        document.body,
      )}
    </div>
  );
}
