import { useState } from "react";
import { MobileHeader } from "./mobile-header";
import { SettingsNavigation } from "./settings-navigation";
import { SettingsNavRenderedItem } from "#/hooks/use-settings-nav-items";
import { settingsLayoutMainScrollClassName } from "#/utils/settings-like-page-layout-classes";

interface SettingsLayoutProps {
  children: React.ReactNode;
  navigationItems: SettingsNavRenderedItem[];
}

/**
 * Mirrors agent-canvas settings layout: sticky aside + scrolling main with
 * max-width content column and right gutter.
 */
export function SettingsLayout({
  children,
  navigationItems,
}: SettingsLayoutProps) {
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const toggleMobileMenu = () => setIsMobileMenuOpen(!isMobileMenuOpen);
  const closeMobileMenu = () => setIsMobileMenuOpen(false);

  return (
    <div className="flex h-full flex-col md:pt-8">
      <MobileHeader
        isMobileMenuOpen={isMobileMenuOpen}
        onToggleMenu={toggleMobileMenu}
      />
      <div className="flex min-h-0 flex-1 gap-10 md:items-start">
        <SettingsNavigation
          isMobileMenuOpen={isMobileMenuOpen}
          onCloseMobileMenu={closeMobileMenu}
          navigationItems={navigationItems}
        />
        <main className={settingsLayoutMainScrollClassName}>
          <div className="mx-auto w-full min-w-0 max-w-[800px]">{children}</div>
        </main>
      </div>
    </div>
  );
}
