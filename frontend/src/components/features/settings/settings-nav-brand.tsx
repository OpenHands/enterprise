import { FaChevronLeft } from "react-icons/fa6";
import { useTranslation } from "react-i18next";
import { InstanceLogoMark } from "#/components/features/super-admin/instance-logo-mark";
import OpenHandsLogoSidebar from "#/assets/branding/openhands-logo-sidebar.svg?react";
import { getAgentCanvasBannerLink } from "#/components/features/home/home-header/agent-canvas-banner";
import { useInstanceSettings } from "#/hooks/query/use-super-admin";
import { I18nKey } from "#/i18n/declaration";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { ORG_QUERY_PARAM } from "#/utils/org/org-url-param";
import { cn } from "#/utils/utils";

/** Same mark + size as agent-canvas sidebar (`SIDEBAR_LOGO_*`). */
const SIDEBAR_LOGO_WIDTH = 34;
const SIDEBAR_LOGO_HEIGHT = Math.round((SIDEBAR_LOGO_WIDTH * 30) / 46);

interface SettingsNavBrandProps {
  className?: string;
}

/**
 * Settings rail brand — logo aligned like agent-canvas sidebar (18px icon slot
 * with overhanging mark), "Account" label, Back to App on the right. The
 * instance's company logo and name replace the mark and the label.
 */
export function SettingsNavBrand({ className }: SettingsNavBrandProps) {
  const { t } = useTranslation();
  const organizationId = useSelectedOrganizationStore(
    (state) => state.organizationId,
  );
  const { data: instanceSettings, isLoading: instanceSettingsLoading } =
    useInstanceSettings();
  const companyName = instanceSettings?.company_name?.trim();
  // Agent Canvas keeps its own org choice, so name the one selected here.
  // Canvas links into Settings the same way, with `?org=`.
  const canvasUrl = new URL(getAgentCanvasBannerLink(window.location).url);
  if (organizationId) {
    canvasUrl.searchParams.set(ORG_QUERY_PARAM, organizationId);
  }
  const canvasHref = canvasUrl.toString();

  return (
    <div
      className={cn(
        "flex h-10 min-h-10 w-full min-w-0 items-center gap-2",
        className,
      )}
    >
      {/* 18px column + overflow-visible — same as agent-canvas sidebar logo.
          A company logo saved by a Super Admin takes the mark's place. */}
      <div className="mr-3 flex shrink-0 items-center">
        <div className="flex h-9 w-[18px] items-center justify-center overflow-visible">
          <InstanceLogoMark
            fallback={
              <OpenHandsLogoSidebar
                data-testid="openhands-brand-mark"
                width={SIDEBAR_LOGO_WIDTH}
                height={SIDEBAR_LOGO_HEIGHT}
                className="max-w-none shrink-0"
                aria-hidden
              />
            }
          />
        </div>
      </div>
      <span
        data-testid="settings-nav-brand-label"
        className="min-w-0 flex-1 truncate text-sm font-medium text-white"
      >
        {companyName ||
          (instanceSettingsLoading ? null : t(I18nKey.ORG$ACCOUNT))}
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
