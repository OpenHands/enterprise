import { useState } from "react";
import { MobileHeader } from "./mobile-header";
import { SettingsDesktopSidebar } from "./settings-desktop-sidebar";
import { SettingsMobileDrawer } from "./settings-mobile-drawer";
import { SettingsNavRenderedItem } from "#/hooks/use-settings-nav-items";
import {
  settingsLayoutMainContentInsetClassName,
  settingsLayoutMainScrollShellClassName,
} from "#/utils/settings-like-page-layout-classes";
import { cn } from "#/utils/utils";

interface SettingsLayoutProps {
  children: React.ReactNode;
  navigationItems: SettingsNavRenderedItem[];
  /** Full-width strip rendered at the top of the main content pane. */
  topBanner?: React.ReactNode;
}

/**
 * Mirrors agent-canvas settings layout: sticky desktop aside + scrolling main,
 * with the mobile drawer rendered outside the flex row so `position: fixed`
 * does not affect desktop sizing. Account footer stays pinned in the rail.
 */
export function SettingsLayout({
  children,
  navigationItems,
  topBanner,
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
        <div className="flex min-h-0 min-w-0 flex-1 flex-col self-stretch">
          {/* Outside the scroll shell so the banner is not inset by scrollbar-gutter. */}
          {topBanner}
          <main className={settingsLayoutMainScrollShellClassName}>
            <div
              className={cn(
                settingsLayoutMainContentInsetClassName,
                topBanner && "pt-6 md:pt-6",
              )}
            >
              <div className="mx-auto w-full min-w-0 max-w-[800px]">
                {children}
              </div>
            </div>
          </main>
        </div>
      </div>
    </div>
  );
}
