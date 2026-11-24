import { OrgSelector } from "../org/org-selector";
import {
  filterSettingsNavForSidebar,
  SettingsNavRenderedItem,
} from "#/hooks/use-settings-nav-items";
import { useShouldHideOrgSelector } from "#/hooks/use-should-hide-org-selector";
import { useMe } from "#/hooks/query/use-me";
import { useConfig } from "#/hooks/query/use-config";
import { SUPER_ADMIN_ENTRY_ITEM } from "#/constants/super-admin-nav";
import { canAccessSuperAdminDashboard } from "#/utils/org/super-admin-access";
import { SettingsNavHeader } from "./settings-nav-header";
import { SettingsNavDivider } from "./settings-nav-divider";
import { SettingsNavLink } from "./settings-nav-link";

interface SettingsNavItemsProps {
  navigationItems: SettingsNavRenderedItem[];
  onItemClick?: () => void;
}

/**
 * Shared org selector + nav link list used by the desktop sidebar and mobile drawer.
 * Rows and dividers share pr-2.5 so separators stop with the nav content, not the rail edge.
 */
export function SettingsNavItems({
  navigationItems,
  onItemClick,
}: SettingsNavItemsProps) {
  const shouldHideSelector = useShouldHideOrgSelector();
  const { data: me } = useMe();
  const { data: config } = useConfig();
  const showSuperAdmin = canAccessSuperAdminDashboard(
    config?.feature_flags,
    me?.permissions,
  );
  const sidebarItems = filterSettingsNavForSidebar(navigationItems);

  return (
    <>
      {!shouldHideSelector && (
        <div className="mb-2 shrink-0 pr-2.5">
          <OrgSelector />
        </div>
      )}
      <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto pt-0.5 custom-scrollbar-always">
        {/* Instance-level entry for Super Admins, above the org sections. */}
        {showSuperAdmin && (
          <>
            <div className="pr-2.5">
              <SettingsNavLink
                item={SUPER_ADMIN_ENTRY_ITEM}
                onClick={onItemClick}
              />
            </div>
            <div className="pr-2.5">
              <SettingsNavDivider className="my-1.5" />
            </div>
          </>
        )}
        {sidebarItems.map((renderedItem, index) => {
          if (renderedItem.type === "header") {
            return (
              <div key={`header-${renderedItem.text}`} className="pr-2.5">
                <SettingsNavHeader
                  text={renderedItem.text}
                  chip={renderedItem.chip}
                />
              </div>
            );
          }

          if (renderedItem.type === "divider") {
            return (
              <div key={`divider-${index}`} className="pr-2.5">
                <SettingsNavDivider className="my-1.5" />
              </div>
            );
          }

          return (
            <div key={renderedItem.item.to} className="pr-2.5">
              <SettingsNavLink
                item={renderedItem.item}
                onClick={onItemClick}
                disabled={renderedItem.disabled}
                disabledAgentName={renderedItem.disabledAgentName}
              />
            </div>
          );
        })}
      </div>
    </>
  );
}
