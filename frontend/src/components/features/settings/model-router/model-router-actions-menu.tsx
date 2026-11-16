import React from "react";
import ReactDOM from "react-dom";
import { useTranslation } from "react-i18next";
import { ContextMenu } from "#/ui/context-menu";
import { ContextMenuListItem } from "#/components/features/context-menu/context-menu-list-item";
import { ConversationNameContextMenuIconText } from "#/components/features/conversation/conversation-name-context-menu-icon-text";
import { I18nKey } from "#/i18n/declaration";
import SettingsIcon from "#/icons/settings.svg?react";
import DeleteIcon from "#/icons/u-delete.svg?react";
import CheckmarkIcon from "#/icons/checkmark.svg?react";
import CloseIcon from "#/icons/close.svg?react";

interface ModelRouterActionsMenuProps {
  onEdit: () => void;
  onSetActive: () => void;
  onDeactivate: () => void;
  onDelete: () => void;
  isActive: boolean;
  isBusy: boolean;
  onClose: () => void;
  /** Trigger element to anchor against; the menu portals to the body. */
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

/** Row menu for one Model Router: Edit, Set active / Deactivate, Delete. */
export function ModelRouterActionsMenu({
  onEdit,
  onSetActive,
  onDeactivate,
  onDelete,
  isActive,
  isBusy,
  onClose,
  anchorRef,
}: ModelRouterActionsMenuProps) {
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
      testId: "model-router-edit",
      icon: SettingsIcon,
      label: t(I18nKey.SETTINGS$PROFILE_EDIT),
      onSelect: onEdit,
    },
    isActive
      ? {
          testId: "model-router-deactivate",
          icon: CloseIcon,
          label: t(I18nKey.SETTINGS$MODEL_ROUTER_DEACTIVATE),
          onSelect: onDeactivate,
          isDisabled: isBusy,
        }
      : {
          testId: "model-router-set-active",
          icon: CheckmarkIcon,
          label: t(I18nKey.SETTINGS$PROFILE_SET_ACTIVE),
          onSelect: onSetActive,
          isDisabled: isBusy,
        },
    {
      testId: "model-router-delete",
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
        testId="model-router-actions-menu"
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
