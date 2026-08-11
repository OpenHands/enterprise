import React from "react";
import { useTranslation } from "react-i18next";
import { IoLogOutOutline } from "react-icons/io5";
import { useGitUser } from "#/hooks/query/use-git-user";
import { useSettings } from "#/hooks/query/use-settings";
import { useLogout } from "#/hooks/mutation/use-logout";
import { useAppMode } from "#/hooks/use-app-mode";
import { UserAvatar } from "#/components/features/sidebar/user-avatar";
import { ContextMenuListItem } from "#/components/features/context-menu/context-menu-list-item";
import { useClickOutsideElement } from "#/hooks/use-click-outside-element";
import DocumentIcon from "#/icons/document.svg?react";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";

const menuItemClassName = cn(
  "flex items-center gap-2 px-2.5 h-9 rounded-md text-sm",
  "text-[var(--oh-muted)] hover:text-white hover:bg-[var(--oh-surface-raised)]",
  "transition-none",
);

/**
 * Settings-nav footer: avatar + label with a popover for docs / logout.
 * Intentionally slim — settings links already live in the nav above.
 */
export function SettingsNavUserMenu() {
  const { t } = useTranslation();
  const user = useGitUser();
  const { data: settings } = useSettings();
  const { mutate: logout } = useLogout();
  const { isSaas } = useAppMode();
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

  return (
    <div
      ref={menuRef}
      data-testid="settings-nav-user-menu"
      className="relative shrink-0 border-t border-[var(--oh-border-subtle)] pt-3"
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
        <UserAvatar
          avatarUrl={user.data?.avatar_url}
          isLoading={user.isFetching}
        />
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
            "absolute bottom-full left-0 right-0 mb-2 z-[9999]",
            "rounded-xl border border-[var(--oh-border-subtle)] bg-surface-deep p-2",
            "context-menu-box-shadow",
          )}
        >
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
