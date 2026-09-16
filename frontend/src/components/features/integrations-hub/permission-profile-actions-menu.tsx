import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import {
  dropdownMenuListClassName,
  dropdownMenuPanelPaddingClassName,
  dropdownMenuRowClassName,
} from "#/utils/dropdown-classes";
import { cn } from "#/utils/utils";

interface PermissionProfileActionsMenuProps {
  profileName: string;
  isDefault?: boolean;
  isDeleteDisabled?: boolean;
  anchorRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  onEditPermissions: () => void;
  onDuplicate: () => void;
  onLoadProfile: () => void;
  onUpdateFromCurrent: () => void;
  onSetDefault: () => void;
  onDelete: () => void;
}

export function PermissionProfileActionsMenu({
  profileName,
  isDefault = false,
  isDeleteDisabled = false,
  anchorRef,
  onClose,
  onEditPermissions,
  onDuplicate,
  onLoadProfile,
  onUpdateFromCurrent,
  onSetDefault,
  onDelete,
}: PermissionProfileActionsMenuProps) {
  const { t } = useTranslation();
  const menuRef = useRef<HTMLDivElement>(null);
  const [portalStyle, setPortalStyle] = useState<CSSProperties>();
  const anchorElement = anchorRef.current;

  useLayoutEffect(() => {
    if (!anchorElement) {
      return undefined;
    }

    const updatePosition = () => {
      const rect = anchorElement.getBoundingClientRect();
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
  }, [anchorElement]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        menuRef.current?.contains(target) ||
        anchorRef.current?.contains(target)
      ) {
        return;
      }
      onClose();
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    // Defer so the opening click does not immediately close the menu.
    const timeoutId = window.setTimeout(() => {
      document.addEventListener("click", handleClickOutside);
    }, 0);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.clearTimeout(timeoutId);
      document.removeEventListener("click", handleClickOutside);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [anchorRef, onClose]);

  if (typeof document === "undefined" || !portalStyle) {
    return null;
  }

  const handleAction = (action: () => void) => {
    action();
    onClose();
  };

  return createPortal(
    <div
      ref={menuRef}
      role="menu"
      aria-label={t(I18nKey.INTEGRATIONS_HUB$PROFILE_ACTIONS, {
        name: profileName,
      })}
      data-testid="permission-profile-actions-menu"
      style={portalStyle}
      className={cn(
        "rounded-[6px] bg-tertiary context-menu-box-shadow",
        dropdownMenuPanelPaddingClassName,
        dropdownMenuListClassName,
      )}
    >
      <button
        type="button"
        role="menuitem"
        data-testid="permission-profile-edit"
        className={dropdownMenuRowClassName}
        onClick={() => handleAction(onEditPermissions)}
      >
        {t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT)}
      </button>
      <button
        type="button"
        role="menuitem"
        data-testid="permission-profile-duplicate"
        className={dropdownMenuRowClassName}
        onClick={() => handleAction(onDuplicate)}
      >
        {t(I18nKey.INTEGRATIONS_HUB$PROFILE_DUPLICATE)}
      </button>
      <button
        type="button"
        role="menuitem"
        data-testid="permission-profile-load"
        className={dropdownMenuRowClassName}
        onClick={() => handleAction(onLoadProfile)}
      >
        {t(I18nKey.INTEGRATIONS_HUB$PROFILE_LOAD)}
      </button>
      <button
        type="button"
        role="menuitem"
        data-testid="permission-profile-update-from-current"
        className={dropdownMenuRowClassName}
        onClick={() => handleAction(onUpdateFromCurrent)}
      >
        {t(I18nKey.INTEGRATIONS_HUB$PROFILE_UPDATE)}
      </button>
      <button
        type="button"
        role="menuitem"
        data-testid="permission-profile-set-default"
        disabled={isDefault}
        className={dropdownMenuRowClassName}
        onClick={() => handleAction(onSetDefault)}
      >
        {t(I18nKey.INTEGRATIONS_HUB$PROFILE_SET_DEFAULT)}
      </button>
      <button
        type="button"
        role="menuitem"
        data-testid="permission-profile-delete"
        disabled={isDeleteDisabled}
        className={dropdownMenuRowClassName}
        onClick={() => handleAction(onDelete)}
      >
        {t(I18nKey.INTEGRATIONS_HUB$PROFILE_DELETE)}
      </button>
    </div>,
    document.getElementById("portal-root") || document.body,
  );
}
