import React from "react";
import { Link, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { IoLogOutOutline } from "react-icons/io5";
import { ShieldCheck } from "lucide-react";
import { useGitUser } from "#/hooks/query/use-git-user";
import { useSettings } from "#/hooks/query/use-settings";
import { useLogout } from "#/hooks/mutation/use-logout";
import { useMe } from "#/hooks/query/use-me";
import { useConfig } from "#/hooks/query/use-config";
import { useAppMode } from "#/hooks/use-app-mode";
import { UserAvatar } from "#/components/features/sidebar/user-avatar";
import { ContextMenuListItem } from "#/components/features/context-menu/context-menu-list-item";
import { useClickOutsideElement } from "#/hooks/use-click-outside-element";
import DocumentIcon from "#/icons/document.svg?react";
import { I18nKey } from "#/i18n/declaration";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import {
  getSettingsUserMenuItems,
  useSettingsNavItems,
} from "#/hooks/use-settings-nav-items";
import { canAccessSuperAdminDashboard } from "#/utils/org/super-admin-access";
import { cn } from "#/utils/utils";
import {
  dropdownMenuListClassName,
  dropdownMenuPanelPaddingClassName,
  dropdownMenuRowClassName,
} from "#/utils/dropdown-classes";

const menuItemClassName = cn(dropdownMenuRowClassName, "h-9 px-2.5");
const menuDividerClassName = "my-0.5 border-t border-[var(--oh-border)]";

/**
 * Settings-nav footer: avatar + label with a popover for account settings,
 * docs, and logout. User / Application live here until they get a better home.
 */
export function SettingsNavUserMenu() {
  const { t } = useTranslation();
  const user = useGitUser();
  const { data: settings } = useSettings();
  const { data: me } = useMe();
  const { data: config } = useConfig();
  const { mutate: logout } = useLogout();
  const { isSaas } = useAppMode();
  const navigate = useNavigate();
  const accountSettings = getSettingsUserMenuItems(useSettingsNavItems());
  const showSuperAdmin = canAccessSuperAdminDashboard(
    config?.feature_flags,
    me?.permissions,
  );
  const [isOpen, setIsOpen] = React.useState(false);
  const menuRef = useClickOutsideElement<HTMLDivElement>(() =>
    setIsOpen(false),
  );

  const displayName =
    settings?.email ||
    settings?.git_user_name ||
    user.data?.login ||
    t(I18nKey.ORG$ACCOUNT);

  const handleLogout = () => {
    logout();
    setIsOpen(false);
  };

  const handleNavigate = (to: string) => {
    setIsOpen(false);
    navigate(to);
  };

  return (
    <div
      ref={menuRef}
      data-testid="settings-nav-user-menu"
      className="relative shrink-0"
    >
      <button
        type="button"
        data-testid="settings-nav-user-trigger"
        onClick={() => setIsOpen((open) => !open)}
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm",
          "text-[var(--oh-muted)] hover:text-white hover:bg-[var(--oh-surface-raised)]",
          "transition-none",
          isOpen && "bg-tertiary text-white",
        )}
        aria-expanded={isOpen}
        aria-haspopup="menu"
      >
        <UserAvatar interactive={false} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm text-white">{displayName}</p>
          <p className="truncate text-xs text-[var(--oh-muted)]">
            {t(I18nKey.ORG$ACCOUNT)}
          </p>
        </div>
      </button>

      {isOpen && (
        <div
          role="menu"
          data-testid="settings-nav-user-popover"
          className={cn(
            "absolute bottom-full left-0 right-0 z-[9999] mb-2",
            "rounded-[6px] bg-tertiary context-menu-box-shadow",
            dropdownMenuPanelPaddingClassName,
            dropdownMenuListClassName,
          )}
        >
          {showSuperAdmin && (
            <>
              <Link
                to={SUPER_ADMIN_PATHS.root}
                role="menuitem"
                data-testid="settings-nav-super-admin"
                onClick={(event) => {
                  event.preventDefault();
                  handleNavigate(SUPER_ADMIN_PATHS.root);
                }}
                className={menuItemClassName}
              >
                <ShieldCheck className="size-4 text-white" strokeWidth={2} />
                {t(I18nKey.SUPER_ADMIN$TITLE)}
              </Link>
              <div className={menuDividerClassName} />
            </>
          )}

          {accountSettings.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              role="menuitem"
              data-testid={`settings-nav-account-${item.to.replace("/settings/", "")}`}
              onClick={(event) => {
                event.preventDefault();
                handleNavigate(item.to);
              }}
              className={menuItemClassName}
            >
              {item.icon}
              {t(item.text as I18nKey)}
            </Link>
          ))}

          {accountSettings.length > 0 && (
            <div className={menuDividerClassName} />
          )}

          <a
            href="https://docs.openhands.dev"
            target="_blank"
            rel="noopener noreferrer"
            role="menuitem"
            onClick={() => setIsOpen(false)}
            className={menuItemClassName}
          >
            <DocumentIcon className="text-white" width={16} height={16} />
            {t(I18nKey.SIDEBAR$DOCS)}
          </a>

          {isSaas && (
            <ContextMenuListItem
              onClick={handleLogout}
              className={menuItemClassName}
            >
              <IoLogOutOutline className="text-white" size={16} />
              {t(I18nKey.ACCOUNT_SETTINGS$LOGOUT)}
            </ContextMenuListItem>
          )}
        </div>
      )}
    </div>
  );
}
