import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import {
  dropdownMenuListClassName,
  dropdownMenuPanelPaddingClassName,
  dropdownMenuRowClassName,
} from "#/utils/dropdown-classes";
import { formControlButtonClassName } from "#/utils/form-control-classes";
import { cn } from "#/utils/utils";

interface ApprovalsDecisionMenuProps {
  canSelectAll: boolean;
  canDeselect: boolean;
  canApprove: boolean;
  canDeny: boolean;
  onSelectAll: () => void;
  onDeselect: () => void;
  onApprove: () => void;
  onDeny: () => void;
}

export function ApprovalsDecisionMenu({
  canSelectAll,
  canDeselect,
  canApprove,
  canDeny,
  onSelectAll,
  onDeselect,
  onApprove,
  onDeny,
}: ApprovalsDecisionMenuProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const isDisabled = !canSelectAll && !canDeselect && !canApprove && !canDeny;

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const onMouseDown = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [open]);

  const close = () => setOpen(false);

  return (
    <div
      ref={containerRef}
      className="relative shrink-0 self-end"
      data-testid="approvals-decision-menu"
    >
      <button
        type="button"
        data-testid="approvals-decision-trigger"
        disabled={isDisabled}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t(I18nKey.INTEGRATIONS_HUB$BULK_ACTIONS)}
        onClick={() => setOpen((current) => !current)}
        className={cn(
          formControlButtonClassName,
          "shrink-0 whitespace-nowrap border border-[var(--oh-border)] bg-base-secondary text-white hover:bg-surface-raised",
        )}
      >
        <span>{t(I18nKey.INTEGRATIONS_HUB$BULK_ACTIONS)}</span>
        <ChevronDown
          className={cn("size-4 shrink-0", open && "rotate-180")}
          aria-hidden
        />
      </button>
      {open && !isDisabled ? (
        <div
          role="menu"
          data-testid="approvals-decision-menu-panel"
          className={cn(
            "absolute right-0 top-full z-50 mt-1 w-max min-w-full",
            "rounded-[6px] bg-tertiary context-menu-box-shadow",
            dropdownMenuPanelPaddingClassName,
          )}
        >
          <ul className={dropdownMenuListClassName}>
            <li>
              <button
                type="button"
                role="menuitem"
                disabled={!canSelectAll}
                className={dropdownMenuRowClassName}
                onClick={() => {
                  onSelectAll();
                  close();
                }}
              >
                {t(I18nKey.INTEGRATIONS_HUB$SELECT_ALL)}
              </button>
            </li>
            <li>
              <button
                type="button"
                role="menuitem"
                disabled={!canDeselect}
                className={dropdownMenuRowClassName}
                onClick={() => {
                  onDeselect();
                  close();
                }}
              >
                {t(I18nKey.INTEGRATIONS_HUB$DESELECT_ALL)}
              </button>
            </li>
            <li>
              <button
                type="button"
                role="menuitem"
                disabled={!canApprove}
                className={dropdownMenuRowClassName}
                onClick={() => {
                  onApprove();
                  close();
                }}
              >
                {t(I18nKey.INTEGRATIONS_HUB$APPROVE_SELECTED)}
              </button>
            </li>
            <li>
              <button
                type="button"
                role="menuitem"
                disabled={!canDeny}
                className={cn(
                  dropdownMenuRowClassName,
                  "text-[var(--oh-danger)] hover:text-[var(--oh-danger)]",
                )}
                onClick={() => {
                  onDeny();
                  close();
                }}
              >
                {t(I18nKey.INTEGRATIONS_HUB$DENY_SELECTED)}
              </button>
            </li>
          </ul>
        </div>
      ) : null}
    </div>
  );
}
