import { useRef } from "react";
import { cn } from "#/utils/utils";
import { useResizableSidebarWidth } from "#/hooks/use-resizable-sidebar-width";
import { SettingsNavRenderedItem } from "#/hooks/use-settings-nav-items";
import { SettingsNavBrand } from "./settings-nav-brand";
import { SettingsNavItems } from "./settings-nav-items";
import { SettingsNavUserMenu } from "./settings-nav-user-menu";
import { SettingsRailResizeHandle } from "./settings-rail-resize-handle";

interface SettingsDesktopSidebarProps {
  navigationItems: SettingsNavRenderedItem[];
}

/**
 * Desktop settings rail — conversation-sidebar drawer chrome (agent-canvas):
 * right border, pl-2.5 / pr-0, scrolling links, sticky account footer with
 * full-bleed top rule to the right edge. Width and text size follow the
 * agent-canvas sidebar, including the width the user dragged it to there.
 */
export function SettingsDesktopSidebar({
  navigationItems,
}: SettingsDesktopSidebarProps) {
  const railRef = useRef<HTMLElement>(null);
  const { width, isDragging, handleMouseDown } =
    useResizableSidebarWidth(railRef);

  return (
    <aside
      ref={railRef}
      data-testid="settings-navbar-desktop"
      className={cn(
        "relative hidden md:flex md:sidebar-expanded-width md:shrink-0 md:flex-col sidebar-text-scale",
        "md:sticky md:top-0 md:self-stretch md:h-full",
        "md:border-r md:border-[var(--oh-border)] md:bg-base md:pb-2 md:pl-2.5 md:pr-0",
      )}
      style={{ "--sidebar-width": `${width}px` } as React.CSSProperties}
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

      <SettingsRailResizeHandle
        onMouseDown={handleMouseDown}
        isDragging={isDragging}
      />
    </aside>
  );
}
