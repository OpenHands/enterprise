/* eslint-disable react/jsx-props-no-spreading */
import { Fragment, type CSSProperties, type ReactNode } from "react";
import { Check } from "lucide-react";
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
  header?: ReactNode;
  footer?: ReactNode;
  onHeaderClick?: () => void;
  onFooterClick?: () => void;
  /** Fixed coordinates when the menu is portaled out of its trigger. */
  style?: CSSProperties;
  /** Draw a check beside the selected option. */
  showSelectionCheck?: boolean;
}

export function DropdownMenu({
  isOpen,
  filteredOptions,
  selectedItem,
  emptyMessage,
  getMenuProps,
  getItemProps,
  header,
  footer,
  onHeaderClick,
  onFooterClick,
  style,
  showSelectionCheck = false,
}: DropdownMenuProps) {
  return (
    <div
      style={style}
      className={cn(
        "flex max-h-60 flex-col overflow-hidden text-white",
        style ? "fixed" : "absolute z-50 mt-1 w-full",
        "bg-tertiary rounded-[6px] context-menu-box-shadow",
        dropdownMenuPanelPaddingClassName,
        !isOpen && "hidden",
      )}
    >
      {isOpen && header ? (
        <div
          className="mb-1 shrink-0 border-b border-[var(--oh-border)] pb-1"
          onMouseDown={(event) => {
            event.preventDefault();
          }}
          onClick={onHeaderClick}
        >
          {header}
        </div>
      ) : null}
      <ul
        {...getMenuProps({
          className: cn(
            "min-h-0 flex-1 overflow-auto p-0",
            dropdownMenuListClassName,
          ),
        })}
      >
        {isOpen && filteredOptions.length === 0 && (
          <li className="px-2 py-2 text-sm text-[var(--oh-muted)] italic">
            {emptyMessage}
          </li>
        )}
        {isOpen &&
          filteredOptions.map((option, index) => (
            <Fragment key={option.value}>
              {option.divider ? (
                <li
                  role="separator"
                  aria-hidden
                  className="my-1 list-none border-t border-[var(--oh-border)]"
                />
              ) : null}
              <li
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
                <span className="min-w-0 flex-1 truncate">{option.label}</span>
                {showSelectionCheck ? (
                  <Check
                    aria-hidden
                    className={cn(
                      "size-3.5 shrink-0",
                      selectedItem?.value === option.value
                        ? "opacity-100"
                        : "opacity-0",
                    )}
                  />
                ) : null}
              </li>
            </Fragment>
          ))}
      </ul>
      {isOpen && footer ? (
        <div
          className="mt-1 shrink-0 border-t border-[var(--oh-border)] pt-1"
          onMouseDown={(event) => {
            // Keep the menu in the same pointer gesture so Downshift does not
            // treat this as an outside click before the footer action runs.
            event.preventDefault();
          }}
          onClick={onFooterClick}
        >
          {footer}
        </div>
      ) : null}
    </div>
  );
}
