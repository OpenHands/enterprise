import { cn } from "#/utils/utils";
import { SettingsNavRenderedItem } from "#/hooks/use-settings-nav-items";
import { SettingsNavBrand } from "./settings-nav-brand";
import { SettingsNavItems } from "./settings-nav-items";
import { SettingsNavUserMenu } from "./settings-nav-user-menu";

interface SettingsDesktopSidebarProps {
  navigationItems: SettingsNavRenderedItem[];
}

/**
 * Desktop settings rail — conversation-sidebar drawer chrome (agent-canvas):
 * right border, pl-2.5 / pr-0, scrolling links, sticky account footer with
 * full-bleed top rule to the right edge.
 */
export function SettingsDesktopSidebar({
  navigationItems,
}: SettingsDesktopSidebarProps) {
  return (
    <aside
      data-testid="settings-navbar-desktop"
      className={cn(
        "hidden md:flex md:w-[300px] md:min-w-[300px] md:shrink-0 md:flex-col md:gap-2",
        "md:sticky md:top-0 md:self-stretch md:h-full",
        "md:border-r md:border-[var(--oh-border)] md:bg-base md:pb-2 md:pl-2.5 md:pr-0",
      )}
    >
      {/* Header row padding matches agent-canvas sidebarHeaderRowClassName. */}
      <SettingsNavBrand className="shrink-0 pl-2.5 pr-2.5" />

      <SettingsNavItems navigationItems={navigationItems} />

      <div
        className={cn(
          "sticky bottom-0 mt-auto flex shrink-0 flex-col items-stretch bg-base",
          // Full-bleed the top rule to the rail's right border (cancels aside pl-2.5).
          "-ml-2.5 w-[calc(100%+0.625rem)] border-t border-[var(--oh-border)] pt-2",
        )}
      >
        <div className="px-2.5">
          <SettingsNavUserMenu />
        </div>
      </div>
    </aside>
  );
}
