import React from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown } from "lucide-react";
import { OrganizationMember, OrganizationUserRole } from "#/types/org";
import { cn } from "#/utils/utils";
import { I18nKey } from "#/i18n/declaration";
import { settingsListRowClassName } from "#/utils/settings-list-classes";
import { OrganizationMemberRoleContextMenu } from "./organization-member-role-context-menu";

interface OrganizationMemberListItemProps {
  email: OrganizationMember["email"];
  role: OrganizationMember["role"];
  status: OrganizationMember["status"];
  isSuperadmin?: boolean;
  hasPassword?: boolean;
  showPasswordState?: boolean;
  hasPermissionToChangeRole: boolean;
  availableRolesToChangeTo: OrganizationUserRole[];

  onRoleChange: (role: OrganizationUserRole) => void;
  onRemove?: () => void;
  onResetPassword?: () => void;
}

export function OrganizationMemberListItem({
  email,
  role,
  status,
  isSuperadmin,
  hasPassword,
  showPasswordState,
  hasPermissionToChangeRole,
  availableRolesToChangeTo,
  onRoleChange,
  onRemove,
  onResetPassword,
}: OrganizationMemberListItemProps) {
  const { t } = useTranslation();
  const [contextMenuOpen, setContextMenuOpen] = React.useState(false);
  const roleTriggerRef = React.useRef<HTMLSpanElement>(null);

  const menuIsPermitted =
    status !== "invited" &&
    (hasPermissionToChangeRole ||
      Boolean(onRemove) ||
      Boolean(onResetPassword));

  const handleRoleClick = (event: React.MouseEvent<HTMLSpanElement>) => {
    if (menuIsPermitted) {
      event.preventDefault();
      event.stopPropagation();
      setContextMenuOpen((open) => !open);
    }
  };

  return (
    <div className={cn(settingsListRowClassName, "justify-between")}>
      <div className="flex min-w-0 items-center gap-2">
        <span
          className={cn(
            "truncate text-sm font-normal leading-5",
            status === "invited" ? "text-muted" : "text-white",
          )}
        >
          {email}
        </span>

        {status === "invited" && (
          <span className="shrink-0 rounded-lg border border-[var(--oh-border)] px-2 py-0.5 text-xs text-muted">
            {t(I18nKey.ORG$STATUS_INVITED)}
          </span>
        )}
        {isSuperadmin && (
          <span className="shrink-0 rounded-lg border border-primary px-2 py-0.5 text-xs text-primary">
            {t(I18nKey.ORG$SUPERADMIN)}
          </span>
        )}
        {showPasswordState && (
          <span
            className={cn(
              "shrink-0 rounded-lg border px-2 py-0.5 text-xs",
              hasPassword
                ? "border-success text-success"
                : "border-amber-500 text-amber-500",
            )}
          >
            {hasPassword
              ? t(I18nKey.ORG$PASSWORD_SET)
              : t(I18nKey.ORG$PASSWORD_SETUP_REQUIRED)}
          </span>
        )}
      </div>

      <div className="relative shrink-0">
        <span
          ref={roleTriggerRef}
          onClick={handleRoleClick}
          className={cn(
            "flex items-center gap-1 text-xs font-normal leading-4 text-muted capitalize",
            menuIsPermitted ? "cursor-pointer" : "cursor-not-allowed",
          )}
        >
          {role}
          {menuIsPermitted && <ChevronDown size={14} />}
        </span>

        {menuIsPermitted && contextMenuOpen && (
          <OrganizationMemberRoleContextMenu
            anchorRef={roleTriggerRef}
            onClose={() => setContextMenuOpen(false)}
            onRoleChange={onRoleChange}
            onRemove={onRemove}
            onResetPassword={onResetPassword}
            availableRolesToChangeTo={availableRolesToChangeTo}
          />
        )}
      </div>
    </div>
  );
}
