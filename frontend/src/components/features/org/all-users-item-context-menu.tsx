import React from "react";
import ReactDOM from "react-dom";
import { useTranslation } from "react-i18next";
import { KeyRound } from "lucide-react";
import { I18nKey } from "#/i18n/declaration";
import { ContextMenu } from "#/ui/context-menu";
import { ContextMenuListItem } from "../context-menu/context-menu-list-item";
import { ContextMenuIconText } from "#/ui/context-menu-icon-text";
import DeleteIcon from "#/icons/u-delete.svg?react";

interface AllUsersItemContextMenuProps {
  onClose: () => void;
  /** Omitted when ``enable_integrated_idp`` is off -- there is no local
   * password login to reset into. */
  onCreatePasswordResetLink?: () => void;
  /** Omitted for the caller's own row -- a super admin cannot remove
   * themselves from here. */
  onRemove?: () => void;
  /**
   * Trigger element to anchor against. The menu portals to document body with
   * fixed positioning so overflow on the users list cannot clip it.
   */
  anchorRef: React.RefObject<HTMLElement | null>;
}

/**
 * Per-row menu for the instance-wide "All Users" view (shown to a super
 * admin when their Personal Workspace is selected -- see
 * ``AllUsersSection``). Counterpart to
 * ``OrganizationMemberRoleContextMenu``, minus the role-change options:
 * there is no org context here to hold an org-scoped role in.
 */
export function AllUsersItemContextMenu({
  onClose,
  onCreatePasswordResetLink,
  onRemove,
  anchorRef,
}: AllUsersItemContextMenuProps) {
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

  const handleCreatePasswordResetLinkClick = (
    event: React.MouseEvent<HTMLButtonElement>,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    onCreatePasswordResetLink?.();
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
        testId="all-users-item-context-menu"
        theme="default"
        className="!static !top-auto !right-auto !mt-0 min-h-fit min-w-[195px] max-w-[260px]"
      >
        {onCreatePasswordResetLink && (
          <ContextMenuListItem
            testId="create-password-reset-link-option"
            onClick={handleCreatePasswordResetLinkClick}
          >
            <ContextMenuIconText
              icon={<KeyRound size={16} className="text-white" />}
              text={t(I18nKey.ORG$CREATE_PASSWORD_RESET_LINK)}
            />
          </ContextMenuListItem>
        )}
        {onRemove && (
          <ContextMenuListItem
            testId="remove-option"
            onClick={handleRemoveClick}
          >
            <ContextMenuIconText
              icon={
                <DeleteIcon width={16} height={16} className="text-red-500" />
              }
              text={t(I18nKey.ORG$REMOVE)}
              className="text-red-500 capitalize"
            />
          </ContextMenuListItem>
        )}
      </ContextMenu>
    </div>,
    document.getElementById("portal-root") || document.body,
  );
}
