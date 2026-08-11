import { cn } from "#/utils/utils";
import CloseIcon from "#/icons/close.svg?react";
import { OrgSelector } from "../org/org-selector";
import { SettingsNavRenderedItem } from "#/hooks/use-settings-nav-items";
import { useShouldHideOrgSelector } from "#/hooks/use-should-hide-org-selector";
import { OpenHandsLogoButton } from "#/components/shared/buttons/openhands-logo-button";
import { SettingsNavHeader } from "./settings-nav-header";
import { SettingsNavDivider } from "./settings-nav-divider";
import { SettingsNavLink } from "./settings-nav-link";
import { SettingsNavUserMenu } from "./settings-nav-user-menu";

interface SettingsNavigationProps {
  isMobileMenuOpen: boolean;
  onCloseMobileMenu: () => void;
  navigationItems: SettingsNavRenderedItem[];
}

/**
 * Settings left nav — matches agent-canvas settings desktop sidebar chrome:
 * 260px sticky aside, h-9 rounded-md rows, tertiary active / muted idle.
 */
export function SettingsNavigation({
  isMobileMenuOpen,
  onCloseMobileMenu,
  navigationItems,
}: SettingsNavigationProps) {
  const shouldHideSelector = useShouldHideOrgSelector();

  return (
    <>
      {isMobileMenuOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 md:hidden"
          onClick={onCloseMobileMenu}
        />
      )}
      <nav
        data-testid="settings-navbar"
        className={cn(
          "flex flex-col gap-2 transition-transform duration-300 ease-in-out",
          "fixed inset-0 z-50 w-full bg-base p-4 transform md:transform-none",
          isMobileMenuOpen ? "translate-x-0" : "-translate-x-full",
          "md:relative md:translate-x-0 md:sticky md:top-8 md:self-start",
          "md:w-[260px] md:shrink-0 md:max-h-[calc(100vh-2rem)] md:p-0 md:pl-8 md:bg-transparent",
        )}
      >
        <div className="flex items-center justify-between shrink-0 px-2">
          <OpenHandsLogoButton variant="white" showText />
          <button
            type="button"
            onClick={onCloseMobileMenu}
            className="md:hidden p-0.5 hover:bg-[var(--oh-surface-raised)] rounded-md transition-none cursor-pointer"
            aria-label="Close navigation menu"
          >
            <CloseIcon width={32} height={32} />
          </button>
        </div>

        {!shouldHideSelector && <OrgSelector />}

        <div className="flex flex-col gap-0.5 pt-0.5 flex-1 min-h-0 overflow-y-auto custom-scrollbar-always">
          {navigationItems.map((renderedItem, index) => {
            if (renderedItem.type === "header") {
              return (
                <SettingsNavHeader
                  key={`header-${renderedItem.text}`}
                  text={renderedItem.text}
                />
              );
            }

            if (renderedItem.type === "divider") {
              return (
                <SettingsNavDivider
                  key={`divider-${index}`}
                  className="my-1.5"
                />
              );
            }

            return (
              <SettingsNavLink
                key={renderedItem.item.to}
                item={renderedItem.item}
                onClick={onCloseMobileMenu}
                disabled={renderedItem.disabled}
                disabledAgentName={renderedItem.disabledAgentName}
              />
            );
          })}
        </div>

        <SettingsNavUserMenu />
      </nav>
    </>
  );
}
