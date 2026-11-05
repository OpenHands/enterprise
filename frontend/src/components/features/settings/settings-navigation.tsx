import { SettingsNavRenderedItem } from "#/hooks/use-settings-nav-items";
import { SettingsDesktopSidebar } from "./settings-desktop-sidebar";
import { SettingsMobileDrawer } from "./settings-mobile-drawer";

interface SettingsNavigationProps {
  isMobileMenuOpen: boolean;
  onCloseMobileMenu: () => void;
  navigationItems: SettingsNavRenderedItem[];
}

/**
 * Settings left nav — desktop sticky rail + mobile drawer, matching agent-canvas.
 */
export function SettingsNavigation({
  isMobileMenuOpen,
  onCloseMobileMenu,
  navigationItems,
}: SettingsNavigationProps) {
  return (
    <>
      <SettingsDesktopSidebar navigationItems={navigationItems} />
      <SettingsMobileDrawer
        isMobileMenuOpen={isMobileMenuOpen}
        onCloseMobileMenu={onCloseMobileMenu}
        navigationItems={navigationItems}
      />
    </>
  );
}
