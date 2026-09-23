import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { HubSearchField } from "#/components/features/integrations-hub/hub-search-field";
import { ContextMenuListItem } from "#/components/features/context-menu/context-menu-list-item";
import { ContextMenu } from "#/ui/context-menu";
import { Typography } from "#/ui/typography";
import ThreeDotsVerticalIcon from "#/icons/three-dots-vertical.svg?react";
import { cn } from "#/utils/utils";
import { HorizontalScrollFade } from "#/components/shared/horizontal-scroll-fade";
import {
  settingsListContainerClassName,
  settingsListIconActionButtonClassName,
  settingsListScrollFadeFromClassName,
  settingsListTableCellClassName,
  settingsListTableHeadClassName,
  settingsListTableHeaderCellClassName,
  settingsListTableMinWidthStyle,
  settingsListTableRowClassName,
} from "#/utils/settings-list-classes";
import type { SuperAdminMembership } from "./super-admin-mock";

interface SuperAdminPageHeaderProps {
  title: string;
  subtitle: string;
  action?: ReactNode;
}

export function SuperAdminPageHeader({
  title,
  subtitle,
  action,
}: SuperAdminPageHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-4">
      <header className="min-w-0 space-y-1">
        <Typography.H2>{title}</Typography.H2>
        <p className="text-sm leading-5 text-muted">{subtitle}</p>
      </header>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

interface SuperAdminSearchFieldProps {
  testId: string;
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
}

export function SuperAdminSearchField({
  testId,
  value,
  placeholder,
  onChange,
}: SuperAdminSearchFieldProps) {
  return (
    <HubSearchField
      testId={testId}
      value={value}
      placeholder={placeholder}
      onChange={onChange}
      className="w-full"
    />
  );
}

interface SuperAdminTableColumn<T> {
  key: string;
  header: string;
  className?: string;
  render: (row: T) => ReactNode;
}

interface SuperAdminTableProps<T> {
  testId: string;
  columns: SuperAdminTableColumn<T>[];
  rows: T[];
  getRowKey: (row: T) => string;
  empty: string;
}

export function SuperAdminTable<T>({
  testId,
  columns,
  rows,
  getRowKey,
  empty,
}: SuperAdminTableProps<T>) {
  return (
    <div
      data-testid={testId}
      className={cn(
        settingsListContainerClassName,
        settingsListScrollFadeFromClassName,
      )}
    >
      <HorizontalScrollFade>
        <table
          className="w-full table-fixed border-collapse"
          style={settingsListTableMinWidthStyle(columns.length)}
        >
          <thead className={settingsListTableHeadClassName}>
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  className={cn(
                    settingsListTableHeaderCellClassName,
                    column.className,
                  )}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length}
                  className="px-4 py-6 text-sm text-[var(--oh-muted)]"
                >
                  {empty}
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr
                  key={getRowKey(row)}
                  className={settingsListTableRowClassName}
                >
                  {columns.map((column) => (
                    <td
                      key={column.key}
                      className={cn(
                        settingsListTableCellClassName,
                        column.className,
                      )}
                    >
                      {column.render(row)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </HorizontalScrollFade>
    </div>
  );
}

export interface SuperAdminRowMenuItem {
  label: string;
  onSelect: () => void;
  destructive?: boolean;
  testId?: string;
}

interface SuperAdminRowMenuProps {
  ariaLabel: string;
  testId: string;
  items: SuperAdminRowMenuItem[];
}

export function SuperAdminRowMenu({
  ariaLabel,
  testId,
  items,
}: SuperAdminRowMenuProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);
  const [portalStyle, setPortalStyle] = useState<React.CSSProperties>();

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) {
      return undefined;
    }

    const updatePosition = () => {
      const rect = triggerRef.current!.getBoundingClientRect();
      setPortalStyle({
        position: "fixed",
        zIndex: 9999,
        top: rect.bottom + 8,
        right: window.innerWidth - rect.right,
        width: "max-content",
      });
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        menuRef.current?.contains(target) ||
        triggerRef.current?.contains(target)
      ) {
        return;
      }
      setOpen(false);
    };

    const timeoutId = window.setTimeout(() => {
      document.addEventListener("click", handleClickOutside);
    }, 0);

    return () => {
      window.clearTimeout(timeoutId);
      document.removeEventListener("click", handleClickOutside);
    };
  }, [open]);

  return (
    <div className="flex justify-end">
      <button
        ref={triggerRef}
        type="button"
        aria-label={ariaLabel}
        aria-expanded={open}
        data-testid={testId}
        className={settingsListIconActionButtonClassName}
        onClick={() => setOpen((current) => !current)}
      >
        <ThreeDotsVerticalIcon width={16} height={16} />
      </button>
      {open &&
        portalStyle &&
        createPortal(
          <div style={portalStyle}>
            <ContextMenu
              ref={menuRef}
              testId={`${testId}-menu`}
              theme="popover"
              spacing="none"
              className="min-w-[160px]"
            >
              {items.map((item) => (
                <ContextMenuListItem
                  key={item.label}
                  testId={item.testId}
                  onClick={() => {
                    item.onSelect();
                    setOpen(false);
                  }}
                  className={item.destructive ? "text-red-400" : undefined}
                >
                  {item.label}
                </ContextMenuListItem>
              ))}
            </ContextMenu>
          </div>,
          document.getElementById("portal-root") || document.body,
        )}
    </div>
  );
}

const USER_TABLE_CELL_CLASS_NAME = "h-auto min-h-12 py-3 align-top";

export function SuperAdminUserMemberships({
  memberships,
  field,
  onOrgClick,
}: {
  memberships: SuperAdminMembership[];
  field: "orgName" | "role";
  onOrgClick?: (orgId: string, orgName: string) => void;
}) {
  return (
    <ul className="flex flex-col gap-1">
      {memberships.map((membership) => (
        <li
          key={`${membership.orgId}-${field}`}
          className={
            field === "orgName" ? "truncate leading-5" : "leading-5 capitalize"
          }
        >
          {field === "orgName" && onOrgClick ? (
            <button
              type="button"
              data-testid={`super-admin-user-org-${membership.orgId}`}
              className="block max-w-full truncate text-left hover:underline"
              onClick={() => onOrgClick(membership.orgId, membership.orgName)}
            >
              {membership.orgName}
            </button>
          ) : (
            membership[field]
          )}
        </li>
      ))}
    </ul>
  );
}

export function SuperAdminStatusLabel({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "capitalize",
        (status === "suspended" ||
          status === "removed" ||
          status === "inactive") &&
          "text-[var(--oh-muted)]",
      )}
    >
      {status}
    </span>
  );
}

export { BrandButton, SettingsInput, USER_TABLE_CELL_CLASS_NAME };
