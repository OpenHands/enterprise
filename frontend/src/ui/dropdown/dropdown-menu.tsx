/* eslint-disable react/jsx-props-no-spreading */
import { cn } from "#/utils/utils";
import { DropdownOption } from "./types";
import {
  dropdownMenuListClassName,
  dropdownMenuPanelPaddingClassName,
  dropdownMenuRowClassName,
} from "#/utils/dropdown-classes";

interface DropdownMenuProps {
  isOpen: boolean;
  filteredOptions: DropdownOption[];
  selectedItem: DropdownOption | null;
  emptyMessage: string;
  getMenuProps: (props?: object) => object;
  getItemProps: (props: {
    item: DropdownOption;
    index: number;
    className?: string;
  }) => object;
}

export function DropdownMenu({
  isOpen,
  filteredOptions,
  selectedItem,
  emptyMessage,
  getMenuProps,
  getItemProps,
}: DropdownMenuProps) {
  return (
    <div
      className={cn(
        "absolute z-50 overflow-hidden text-white",
        "w-full mt-1",
        "bg-tertiary rounded-[6px] context-menu-box-shadow",
        dropdownMenuPanelPaddingClassName,
        "max-h-60 overflow-auto",
        !isOpen && "hidden",
      )}
      // Menu-item clicks bubble to any <label> wrapping the Dropdown, whose
      // default action re-clicks the combobox input and reopens the menu
      // right after selection; cancel that default here.
      onClick={(event) => event.preventDefault()}
    >
      <ul
        {...getMenuProps({ className: cn("p-0", dropdownMenuListClassName) })}
      >
        {isOpen && filteredOptions.length === 0 && (
          <li className="px-2 py-2 text-sm text-[var(--oh-muted)] italic">
            {emptyMessage}
          </li>
        )}
        {isOpen &&
          filteredOptions.map((option, index) => (
            <li
              key={option.value}
              {...getItemProps({
                item: option,
                index,
                className: cn(
                  dropdownMenuRowClassName,
                  "focus:outline-none",
                  selectedItem?.value === option.value &&
                    "bg-[var(--oh-interactive-selected)] text-white",
                ),
              })}
            >
              <span className="min-w-0 truncate">{option.label}</span>
            </li>
          ))}
      </ul>
    </div>
  );
}
