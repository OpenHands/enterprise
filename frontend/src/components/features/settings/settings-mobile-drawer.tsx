import { cn } from "#/utils/utils";
import CloseIcon from "#/icons/close.svg?react";
import { SettingsNavRenderedItem } from "#/hooks/use-settings-nav-items";
import { SettingsNavBrand } from "./settings-nav-brand";
import { SettingsNavItems } from "./settings-nav-items";
import { SettingsNavUserMenu } from "./settings-nav-user-menu";

interface SettingsMobileDrawerProps {
  isMobileMenuOpen: boolean;
  onCloseMobileMenu: () => void;
  navigationItems: SettingsNavRenderedItem[];
}

/**
 * Mobile overlay + drawer. Rendered outside the scrolling flex row so `position:
 * fixed` does not interact with flex item sizing on desktop (agent-canvas pattern).
 */
export function SettingsMobileDrawer({
  isMobileMenuOpen,
  onCloseMobileMenu,
  navigationItems,
}: SettingsMobileDrawerProps) {
  return (
    <>
      {isMobileMenuOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={onCloseMobileMenu}
        />
      )}
      <nav
        data-testid="settings-navbar"
        className={cn(
          "flex flex-col gap-2 transition-transform duration-300 ease-in-out",
          "fixed inset-0 z-50 w-full bg-base p-4 transform md:hidden",
          isMobileMenuOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex shrink-0 items-center justify-between gap-2 px-2">
          <SettingsNavBrand className="min-w-0 flex-1" />
          <button
            type="button"
            onClick={onCloseMobileMenu}
            className="cursor-pointer rounded-md p-0.5 hover:bg-[var(--oh-surface-raised)] transition-none"
            aria-label="Close navigation menu"
          >
            <CloseIcon width={32} height={32} />
          </button>
        </div>

        <SettingsNavItems
          navigationItems={navigationItems}
          onItemClick={onCloseMobileMenu}
        />

        <div
          className={cn(
            "sticky bottom-0 mt-auto flex shrink-0 flex-col bg-base",
            "border-t border-[var(--oh-border)] pt-2",
          )}
        >
          <SettingsNavUserMenu />
        </div>
      </nav>
    </>
  );
}
