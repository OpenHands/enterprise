import React from "react";
import ReactDOM from "react-dom";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { ContextMenu } from "#/ui/context-menu";
import { ContextMenuListItem } from "../context-menu/context-menu-list-item";
import { ContextMenuIconText } from "#/ui/context-menu-icon-text";
import { OrganizationUserRole } from "#/types/org";
import UserIcon from "#/icons/user.svg?react";
import DeleteIcon from "#/icons/u-delete.svg?react";
import AdminIcon from "#/icons/admin.svg?react";

interface OrganizationMemberRoleContextMenuProps {
  onClose: () => void;
  onRoleChange: (role: OrganizationUserRole) => void;
  onRemove?: () => void;
  availableRolesToChangeTo: OrganizationUserRole[];
  /**
   * Trigger element to anchor against. The menu portals to document body with
   * fixed positioning so overflow on the members list cannot clip it.
   */
  anchorRef: React.RefObject<HTMLElement | null>;
}

export function OrganizationMemberRoleContextMenu({
  onClose,
  onRoleChange,
  onRemove,
  availableRolesToChangeTo,
  anchorRef,
}: OrganizationMemberRoleContextMenuProps) {
  const { t } = useTranslation();
  const menuRef = React.useRef<HTMLUListElement>(null);
  const [portalStyle, setPortalStyle] = React.useState<React.CSSProperties>();

  const anchorElement = anchorRef.current;

  React.useLayoutEffect(() => {
    if (!anchorElement) {
      return undefined;
    }

    const updatePosition = () => {
      const rect = anchorElement.getBoundingClientRect();
      const gap = 8;
      setPortalStyle({
        position: "fixed",
        zIndex: 9999,
        top: rect.bottom + gap,
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
  }, [anchorElement]);

  React.useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target)) {
        return;
      }
      if (anchorRef.current?.contains(target)) {
        return;
      }
      onClose();
    };

    // Defer so the opening click does not immediately close the menu.
    const timeoutId = window.setTimeout(() => {
      document.addEventListener("click", handleClickOutside);
    }, 0);

    return () => {
      window.clearTimeout(timeoutId);
      document.removeEventListener("click", handleClickOutside);
    };
  }, [anchorRef, onClose]);

  const handleRoleChangeClick = (
    event: React.MouseEvent<HTMLButtonElement>,
    role: OrganizationUserRole,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    onRoleChange(role);
    onClose();
  };

  const handleRemoveClick = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    onRemove?.();
    onClose();
  };

  if (typeof document === "undefined" || !portalStyle) {
    return null;
  }

  return ReactDOM.createPortal(
    <div style={portalStyle}>
      <ContextMenu
        ref={menuRef}
        testId="organization-member-role-context-menu"
        theme="default"
        className="!static !top-auto !right-auto !mt-0 min-h-fit min-w-[195px] max-w-[195px]"
      >
        {availableRolesToChangeTo.includes("owner") && (
          <ContextMenuListItem
            testId="owner-option"
            onClick={(event) => handleRoleChangeClick(event, "owner")}
          >
            <ContextMenuIconText
              icon={
                <AdminIcon
                  width={16}
                  height={16}
                  className="text-white pl-[2px]"
                />
              }
              text={t(I18nKey.ORG$ROLE_OWNER)}
              className="capitalize"
            />
          </ContextMenuListItem>
        )}
        {availableRolesToChangeTo.includes("admin") && (
          <ContextMenuListItem
            testId="admin-option"
            onClick={(event) => handleRoleChangeClick(event, "admin")}
          >
            <ContextMenuIconText
              icon={
                <AdminIcon
                  width={16}
                  height={16}
                  className="text-white pl-[2px]"
                />
              }
              text={t(I18nKey.ORG$ROLE_ADMIN)}
              className="capitalize"
            />
          </ContextMenuListItem>
        )}
        {availableRolesToChangeTo.includes("member") && (
          <ContextMenuListItem
            testId="member-option"
            onClick={(event) => handleRoleChangeClick(event, "member")}
          >
            <ContextMenuIconText
              icon={<UserIcon width={16} height={16} className="text-white" />}
              text={t(I18nKey.ORG$ROLE_MEMBER)}
              className="capitalize"
            />
          </ContextMenuListItem>
        )}
        <ContextMenuListItem testId="remove-option" onClick={handleRemoveClick}>
          <ContextMenuIconText
            icon={
              <DeleteIcon width={16} height={16} className="text-red-500" />
            }
            text={t(I18nKey.ORG$REMOVE)}
            className="text-red-500 capitalize"
          />
        </ContextMenuListItem>
      </ContextMenu>
    </div>,
    document.getElementById("portal-root") || document.body,
  );
}
