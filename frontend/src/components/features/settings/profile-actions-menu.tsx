import React from "react";
import ReactDOM from "react-dom";
import { useTranslation } from "react-i18next";
import { ContextMenu } from "#/ui/context-menu";
import { ContextMenuListItem } from "#/components/features/context-menu/context-menu-list-item";
import { ConversationNameContextMenuIconText } from "#/components/features/conversation/conversation-name-context-menu-icon-text";
import { I18nKey } from "#/i18n/declaration";
import SettingsIcon from "#/icons/settings.svg?react";
import EditIcon from "#/icons/u-edit.svg?react";
import DeleteIcon from "#/icons/u-delete.svg?react";
import CheckmarkIcon from "#/icons/checkmark.svg?react";

interface ProfileActionsMenuProps {
  onEdit: () => void;
  onRename: () => void;
  onSetActive: () => void;
  onDelete: () => void;
  isActive: boolean;
  isActivating: boolean;
  onClose: () => void;
  /**
   * Trigger element to anchor against. The menu portals to document body with
   * fixed positioning so overflow on the profiles list cannot clip it.
   */
  anchorRef: React.RefObject<HTMLElement | null>;
}

type MenuIcon = React.ComponentType<{ width: number; height: number }>;

interface MenuItemSpec {
  testId: string;
  icon: MenuIcon;
  label: string;
  onSelect: () => void;
  isDisabled?: boolean;
  isDestructive?: boolean;
}

export function ProfileActionsMenu({
  onEdit,
  onRename,
  onSetActive,
  onDelete,
  isActive,
  isActivating,
  onClose,
  anchorRef,
}: ProfileActionsMenuProps) {
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

  const items: MenuItemSpec[] = [
    {
      testId: "profile-edit",
      icon: SettingsIcon,
      label: t(I18nKey.SETTINGS$PROFILE_EDIT),
      onSelect: onEdit,
    },
    {
      testId: "profile-rename",
      icon: EditIcon,
      label: t(I18nKey.BUTTON$RENAME),
      onSelect: onRename,
    },
    {
      testId: "profile-set-active",
      icon: CheckmarkIcon,
      label: t(I18nKey.SETTINGS$PROFILE_SET_ACTIVE),
      onSelect: onSetActive,
      isDisabled: isActive || isActivating,
    },
    {
      testId: "profile-delete",
      icon: DeleteIcon,
      label: t(I18nKey.BUTTON$DELETE),
      onSelect: onDelete,
      isDestructive: true,
    },
  ];

  if (typeof document === "undefined" || !portalStyle) {
    return null;
  }

  return ReactDOM.createPortal(
    <div style={portalStyle}>
      <ContextMenu
        ref={menuRef}
        testId="profile-actions-menu"
        theme="default"
        className="!static !top-auto !right-auto !mt-0 min-w-[180px]"
      >
        {items.map(
          ({
            testId,
            icon: Icon,
            label,
            onSelect,
            isDisabled,
            isDestructive,
          }) => (
            <ContextMenuListItem
              key={testId}
              testId={testId}
              onClick={() => {
                onSelect();
                onClose();
              }}
              isDisabled={isDisabled}
              className={isDestructive ? "text-red-400" : undefined}
            >
              <ConversationNameContextMenuIconText
                icon={<Icon width={16} height={16} />}
                text={label}
              />
            </ContextMenuListItem>
          ),
        )}
      </ContextMenu>
    </div>,
    document.getElementById("portal-root") || document.body,
  );
}
