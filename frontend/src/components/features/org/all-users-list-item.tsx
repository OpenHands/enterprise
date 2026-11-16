import React from "react";
import { useTranslation } from "react-i18next";
import { MoreVertical } from "lucide-react";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";
import { settingsListRowClassName } from "#/utils/settings-list-classes";
import { AdminUser } from "#/api/admin-service/admin.types";
import { AllUsersItemContextMenu } from "./all-users-item-context-menu";

interface AllUsersListItemProps {
  user: AdminUser;
  /** Hidden for the caller's own row -- a super admin cannot remove
   * themselves from here. */
  isSelf: boolean;
  /** Only meaningful when `ENABLE_INTEGRATED_IDP` is on -- see
   * `CreatePasswordResetLinkModal`. */
  canCreatePasswordResetLink: boolean;
  onCreatePasswordResetLink: () => void;
  onRemove: () => void;
}

export function AllUsersListItem({
  user,
  isSelf,
  canCreatePasswordResetLink,
  onCreatePasswordResetLink,
  onRemove,
}: AllUsersListItemProps) {
  const { t } = useTranslation();
  const [contextMenuOpen, setContextMenuOpen] = React.useState(false);
  const menuTriggerRef = React.useRef<HTMLButtonElement>(null);

  const menuIsOpenable = canCreatePasswordResetLink || !isSelf;

  return (
    <div className={cn(settingsListRowClassName, "justify-between")}>
      <span className="truncate text-sm font-normal leading-5 text-white">
        {user.email}
      </span>

      <div className="flex shrink-0 items-center gap-2">
        {user.is_super_admin && (
          <span className="shrink-0 rounded-lg border border-[var(--oh-border)] px-2 py-0.5 text-xs capitalize text-muted">
            {t(I18nKey.ORG$ROLE_SUPERADMIN)}
          </span>
        )}

        {menuIsOpenable && (
          <button
            ref={menuTriggerRef}
            type="button"
            data-testid="all-users-item-menu-trigger"
            onClick={() => setContextMenuOpen((open) => !open)}
            className="text-muted hover:text-white"
            aria-label={t(I18nKey.COMMON$MORE_OPTIONS)}
          >
            <MoreVertical size={16} />
          </button>
        )}

        {menuIsOpenable && contextMenuOpen && (
          <AllUsersItemContextMenu
            anchorRef={menuTriggerRef}
            onClose={() => setContextMenuOpen(false)}
            onCreatePasswordResetLink={
              canCreatePasswordResetLink ? onCreatePasswordResetLink : undefined
            }
            onRemove={isSelf ? undefined : onRemove}
          />
        )}
      </div>
    </div>
  );
}
