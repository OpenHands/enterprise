import { FaChevronLeft } from "react-icons/fa6";
import { useTranslation } from "react-i18next";
import OpenHandsLogoSidebar from "#/assets/branding/openhands-logo-sidebar.svg?react";
import { getAgentCanvasBannerLink } from "#/components/features/home/home-header/agent-canvas-banner";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";

/** Same mark + size as agent-canvas sidebar (`SIDEBAR_LOGO_*`). */
const SIDEBAR_LOGO_WIDTH = 34;
const SIDEBAR_LOGO_HEIGHT = Math.round((SIDEBAR_LOGO_WIDTH * 30) / 46);

interface SettingsNavBrandProps {
  className?: string;
}

/**
 * Settings rail brand — logo aligned like agent-canvas sidebar (18px icon slot
 * with overhanging mark), "Settings" label, Back to App on the right.
 */
export function SettingsNavBrand({ className }: SettingsNavBrandProps) {
  const { t } = useTranslation();
  const canvasHref = getAgentCanvasBannerLink(window.location).url;

  return (
    <div
      className={cn(
        "flex h-10 min-h-10 w-full min-w-0 items-center gap-2",
        className,
      )}
    >
      {/* 18px column + overflow-visible — same as agent-canvas sidebar logo. */}
      <div className="mr-3 flex h-9 w-[18px] shrink-0 items-center justify-center overflow-visible">
        <OpenHandsLogoSidebar
          width={SIDEBAR_LOGO_WIDTH}
          height={SIDEBAR_LOGO_HEIGHT}
          className="max-w-none shrink-0"
          aria-hidden
        />
      </div>
      <span className="min-w-0 flex-1 truncate text-sm font-medium text-white">
        {t(I18nKey.SETTINGS$TITLE)}
      </span>
      <a
        href={canvasHref}
        data-testid="settings-back-to-app"
        className={cn(
          "inline-flex shrink-0 items-center gap-1.5",
          "text-xs font-medium text-[var(--oh-muted)] hover:text-white",
          "transition-colors",
        )}
      >
        <FaChevronLeft size={10} aria-hidden="true" />
        {t(I18nKey.SETTINGS$BACK_TO_APP)}
      </a>
    </div>
  );
}
