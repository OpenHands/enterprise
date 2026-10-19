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
  hasPermissionToChangeRole: boolean;
  availableRolesToChangeTo: OrganizationUserRole[];
  /** Only meaningful when `ENABLE_INTEGRATED_IDP` is on -- see
   * `CreatePasswordResetLinkModal`. Independent of
   * `hasPermissionToChangeRole` so a super admin viewing an org they hold
   * no role in can still reach it without also being granted role-change
   * authority over this member. */
  canCreatePasswordResetLink?: boolean;
  onCreatePasswordResetLink?: () => void;

  onRoleChange: (role: OrganizationUserRole) => void;
  onRemove?: () => void;
}

export function OrganizationMemberListItem({
  email,
  role,
  status,
  hasPermissionToChangeRole,
  availableRolesToChangeTo,
  canCreatePasswordResetLink,
  onCreatePasswordResetLink,
  onRoleChange,
  onRemove,
}: OrganizationMemberListItemProps) {
  const { t } = useTranslation();
  const [contextMenuOpen, setContextMenuOpen] = React.useState(false);
  const roleTriggerRef = React.useRef<HTMLSpanElement>(null);

  const menuIsOpenable =
    status !== "invited" &&
    (hasPermissionToChangeRole || !!canCreatePasswordResetLink);

  const handleRoleClick = (event: React.MouseEvent<HTMLSpanElement>) => {
    if (menuIsOpenable) {
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
      </div>

      <div className="relative shrink-0">
        <span
          ref={roleTriggerRef}
          onClick={handleRoleClick}
          className={cn(
            "flex items-center gap-1 text-xs font-normal leading-4 text-muted capitalize",
            menuIsOpenable ? "cursor-pointer" : "cursor-not-allowed",
          )}
        >
          {role}
          {menuIsOpenable && <ChevronDown size={14} />}
        </span>

        {menuIsOpenable && contextMenuOpen && (
          <OrganizationMemberRoleContextMenu
            anchorRef={roleTriggerRef}
            onClose={() => setContextMenuOpen(false)}
            onRoleChange={onRoleChange}
            onRemove={onRemove}
            availableRolesToChangeTo={availableRolesToChangeTo}
            showRoleOptions={hasPermissionToChangeRole}
            showCreatePasswordResetLink={canCreatePasswordResetLink}
            onCreatePasswordResetLink={onCreatePasswordResetLink}
          />
        )}
      </div>
    </div>
  );
}
