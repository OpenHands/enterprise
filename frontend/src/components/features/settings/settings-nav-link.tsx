import { useTranslation } from "react-i18next";
import { NavLink } from "react-router";
import { Tooltip } from "@heroui/react";
import { cn } from "#/utils/utils";
import { I18nKey } from "#/i18n/declaration";
import { SettingsNavItem } from "#/constants/settings-nav";
import {
  SIDEBAR_ICON_SLOT_CLASS,
  SIDEBAR_ROW_INTERACTIVE_CLASS,
  sidebarNavLabelClassName,
  sidebarNavRowClassName,
} from "#/components/features/sidebar/sidebar-layout";

interface SettingsNavLinkProps {
  item: SettingsNavItem;
  onClick: () => void;
  disabled?: boolean;
  disabledAgentName?: string;
}

export function SettingsNavLink({
  item,
  onClick,
  disabled,
  disabledAgentName,
}: SettingsNavLinkProps) {
  const { t } = useTranslation();
  const { to, icon, text } = item;
  const label = t(text as I18nKey);

  if (disabled) {
    const tooltip = disabledAgentName
      ? t(I18nKey.SETTINGS$AGENT_DISABLED_TOOLTIP, {
          agentName: disabledAgentName,
        })
      : undefined;
    return (
      <Tooltip content={tooltip} placement="right">
        <div
          aria-disabled="true"
          data-testid={`settings-nav-disabled-${to}`}
          className={cn(
            sidebarNavRowClassName(),
            SIDEBAR_ROW_INTERACTIVE_CLASS.idle,
            "opacity-50 pointer-events-none cursor-not-allowed",
          )}
        >
          <span className={SIDEBAR_ICON_SLOT_CLASS}>{icon}</span>
          <span className={sidebarNavLabelClassName()}>{label}</span>
        </div>
      </Tooltip>
    );
  }

  return (
    <NavLink
      end
      to={to}
      onClick={onClick}
      data-testid={`sidebar-settings-${to}`}
      className={({ isActive }) =>
        cn(
          sidebarNavRowClassName(),
          isActive
            ? SIDEBAR_ROW_INTERACTIVE_CLASS.active
            : SIDEBAR_ROW_INTERACTIVE_CLASS.idle,
        )
      }
    >
      <span className={SIDEBAR_ICON_SLOT_CLASS}>{icon}</span>
      <span className={sidebarNavLabelClassName()}>{label}</span>
    </NavLink>
  );
}
