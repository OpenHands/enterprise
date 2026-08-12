import { useState } from "react";
import { MobileHeader } from "./mobile-header";
import { SettingsDesktopSidebar } from "./settings-desktop-sidebar";
import { SettingsMobileDrawer } from "./settings-mobile-drawer";
import { SettingsNavRenderedItem } from "#/hooks/use-settings-nav-items";
import { settingsLayoutMainScrollClassName } from "#/utils/settings-like-page-layout-classes";

interface SettingsLayoutProps {
  children: React.ReactNode;
  navigationItems: SettingsNavRenderedItem[];
}

/**
 * Mirrors agent-canvas settings layout: sticky desktop aside + scrolling main,
 * with the mobile drawer rendered outside the flex row so `position: fixed`
 * does not affect desktop sizing. Account footer stays pinned in the rail.
 */
export function SettingsLayout({
  children,
  navigationItems,
}: SettingsLayoutProps) {
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const toggleMobileMenu = () => setIsMobileMenuOpen(!isMobileMenuOpen);
  const closeMobileMenu = () => setIsMobileMenuOpen(false);

  return (
    <div className="flex h-full flex-col">
      <MobileHeader
        isMobileMenuOpen={isMobileMenuOpen}
        onToggleMenu={toggleMobileMenu}
      />
      <SettingsMobileDrawer
        isMobileMenuOpen={isMobileMenuOpen}
        onCloseMobileMenu={closeMobileMenu}
        navigationItems={navigationItems}
      />
      <div className="flex min-h-0 flex-1">
        <SettingsDesktopSidebar navigationItems={navigationItems} />
        <main className={settingsLayoutMainScrollClassName}>
          <div className="mx-auto w-full min-w-0 max-w-[800px]">{children}</div>
        </main>
      </div>
    </div>
  );
}
