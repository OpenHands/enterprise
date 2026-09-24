import { useMemo, useState, useSyncExternalStore } from "react";
import {
  Navigate,
  NavLink,
  Outlet,
  useLocation,
  useMatches,
} from "react-router";
import { useTranslation } from "react-i18next";
import { FaChevronLeft } from "react-icons/fa6";
import { SettingsNavLink } from "#/components/features/settings/settings-nav-link";
import { SettingsNavUserMenu } from "#/components/features/settings/settings-nav-user-menu";
import OpenHandsLogoSidebar from "#/assets/branding/openhands-logo-sidebar.svg?react";
import CloseIcon from "#/icons/close.svg?react";
import { useMe } from "#/hooks/query/use-me";
import { useConfig } from "#/hooks/query/use-config";
import { I18nKey } from "#/i18n/declaration";
import { SuperAdminSetupNav } from "#/components/features/super-admin/super-admin-setup-guide";
import {
  SUPER_ADMIN_NAV_ITEMS,
  SUPER_ADMIN_SETUP_ITEM,
} from "#/constants/super-admin-nav";
import { canAccessSuperAdminDashboard } from "#/utils/org/super-admin-access";
import {
  getSuperAdminNuxPath,
  getSuperAdminNuxStep,
  readSuperAdminNux,
  subscribeSuperAdminNux,
} from "#/utils/org/super-admin-nux";
import {
  getSetupTestSuperAdminAccessOverride,
  readSetupTestPersona,
  subscribeSetupTestPersona,
} from "#/utils/org/setup-test-harness";
import { cn } from "#/utils/utils";
import { Typography } from "#/ui/typography";
import {
  settingsLayoutMainContentInsetClassName,
  settingsLayoutMainScrollShellClassName,
} from "#/utils/settings-like-page-layout-classes";

const SIDEBAR_LOGO_WIDTH = 34;
const SIDEBAR_LOGO_HEIGHT = Math.round((SIDEBAR_LOGO_WIDTH * 30) / 46);

function SuperAdminNavBrand({ className }: { className?: string }) {
  const { t } = useTranslation();

  return (
    <div
      className={cn(
        "flex h-10 min-h-10 w-full min-w-0 items-center gap-2",
        className,
      )}
    >
      <div className="mr-3 flex h-9 w-[18px] shrink-0 items-center justify-center overflow-visible">
        <OpenHandsLogoSidebar
          width={SIDEBAR_LOGO_WIDTH}
          height={SIDEBAR_LOGO_HEIGHT}
          className="max-w-none shrink-0"
          aria-hidden
        />
      </div>
      <span className="min-w-0 flex-1 truncate text-sm font-medium text-white">
        {t(I18nKey.SUPER_ADMIN$TITLE)}
      </span>
      <NavLink
        to="/settings"
        data-testid="super-admin-back-to-settings"
        className={cn(
          "inline-flex shrink-0 items-center gap-1.5",
          "text-xs font-medium text-[var(--oh-muted)] hover:text-white",
          "transition-colors",
        )}
      >
        <FaChevronLeft size={10} aria-hidden="true" />
        {t(I18nKey.SUPER_ADMIN$BACK_TO_SETTINGS)}
      </NavLink>
    </div>
  );
}

function SuperAdminSidebar({ onItemClick }: { onItemClick?: () => void }) {
  return (
    <aside
      data-testid="super-admin-navbar"
      className={cn(
        "hidden md:flex md:w-[300px] md:min-w-[300px] md:shrink-0 md:flex-col",
        "md:sticky md:top-0 md:self-stretch md:h-full",
        "md:border-r md:border-[var(--oh-border)] md:bg-base md:pb-2 md:pl-2.5 md:pr-0",
      )}
    >
      <SuperAdminNavBrand className="shrink-0 pl-2.5 pr-2.5" />
      <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto pt-0.5 custom-scrollbar-always">
        <SuperAdminSetupNav onNavigate={onItemClick} className="pr-2.5" />
        {SUPER_ADMIN_NAV_ITEMS.map((item) => (
          <div key={item.to} className="pr-2.5">
            <SettingsNavLink item={item} onClick={onItemClick} />
          </div>
        ))}
      </div>
      <div
        className={cn(
          "sticky bottom-0 mt-auto flex shrink-0 flex-col items-stretch bg-base",
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

export function SuperAdminLayout() {
  const { t } = useTranslation();
  const location = useLocation();
  const matches = useMatches();
  const { data: me, isLoading, isPending } = useMe();
  const { data: config, isLoading: isConfigLoading } = useConfig();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const nux = useSyncExternalStore(
    subscribeSuperAdminNux,
    readSuperAdminNux,
    readSuperAdminNux,
  );
  // Mock persona override must re-render SA access checks.
  useSyncExternalStore(
    subscribeSetupTestPersona,
    readSetupTestPersona,
    () => "live",
  );
  const routeHandles = matches.map(
    (match) =>
      match.handle as
        | { hideTitle?: boolean; wideContent?: boolean }
        | undefined,
  );
  const shouldHideTitle = routeHandles.some((handle) => handle?.hideTitle);
  const wideContent = routeHandles.some((handle) => handle?.wideContent);

  const currentItem = useMemo(() => {
    const pages = [SUPER_ADMIN_SETUP_ITEM, ...SUPER_ADMIN_NAV_ITEMS];
    return (
      pages.find((item) => item.to === location.pathname) ??
      SUPER_ADMIN_NAV_ITEMS[0]
    );
  }, [location.pathname]);

  // `useMe` is disabled until SaaS config + an org id are ready. In that
  // idle state `isLoading` is false, so wait on `isPending` too or we bounce
  // Super Admins back to Settings before permissions arrive.
  if (isLoading || isPending || isConfigLoading) {
    return <main data-testid="super-admin-screen" className="min-h-0 h-full" />;
  }

  const saAccessOverride = getSetupTestSuperAdminAccessOverride();
  const canAccess =
    saAccessOverride === null
      ? canAccessSuperAdminDashboard(config?.feature_flags, me?.permissions)
      : saAccessOverride;

  if (!canAccess) {
    return <Navigate to="/settings" replace />;
  }

  const nuxStep = getSuperAdminNuxStep(nux);
  if (nuxStep !== "done") {
    return <Navigate to={getSuperAdminNuxPath(nuxStep)} replace />;
  }

  return (
    <main data-testid="super-admin-screen" className="min-h-0 h-full">
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-between px-4 pt-4 mb-2 md:hidden">
          <Typography.H2>{t(I18nKey.SUPER_ADMIN$TITLE)}</Typography.H2>
          <button
            type="button"
            onClick={() => setIsMobileMenuOpen((open) => !open)}
            className="p-2 rounded-md bg-tertiary hover:bg-[var(--oh-surface-raised)] transition-colors"
            aria-label="Toggle Super Admin menu"
          >
            <svg
              width={20}
              height={20}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              {isMobileMenuOpen ? (
                <>
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </>
              ) : (
                <>
                  <line x1="3" y1="6" x2="21" y2="6" />
                  <line x1="3" y1="12" x2="21" y2="12" />
                  <line x1="3" y1="18" x2="21" y2="18" />
                </>
              )}
            </svg>
          </button>
        </div>
        {isMobileMenuOpen && (
          <div
            className="fixed inset-0 z-40 bg-black/50 md:hidden"
            onClick={() => setIsMobileMenuOpen(false)}
          />
        )}
        <nav
          data-testid="super-admin-navbar-mobile"
          className={cn(
            "flex flex-col gap-2 transition-transform duration-300 ease-in-out",
            "fixed inset-0 z-50 w-full bg-base p-4 transform md:hidden",
            isMobileMenuOpen ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <div className="flex shrink-0 items-center justify-between gap-2 px-2">
            <SuperAdminNavBrand className="min-w-0 flex-1" />
            <button
              type="button"
              onClick={() => setIsMobileMenuOpen(false)}
              className="cursor-pointer rounded-md p-0.5 hover:bg-[var(--oh-surface-raised)] transition-none"
              aria-label="Close navigation menu"
            >
              <CloseIcon width={32} height={32} />
            </button>
          </div>
          <SuperAdminSetupNav onNavigate={() => setIsMobileMenuOpen(false)} />
          {SUPER_ADMIN_NAV_ITEMS.map((item) => (
            <SettingsNavLink
              key={item.to}
              item={item}
              onClick={() => setIsMobileMenuOpen(false)}
            />
          ))}
        </nav>
        <div className="flex min-h-0 flex-1">
          <SuperAdminSidebar />
          <div className="flex min-h-0 min-w-0 flex-1 flex-col self-stretch">
            <div className={settingsLayoutMainScrollShellClassName}>
              <div className={settingsLayoutMainContentInsetClassName}>
                <div
                  className={cn(
                    "mx-auto flex w-full min-w-0 flex-col gap-6 pb-8",
                    wideContent ? "max-w-none" : "max-w-[800px]",
                  )}
                >
                  {!shouldHideTitle && (
                    <header className="space-y-1">
                      <Typography.H2>{t(currentItem.text)}</Typography.H2>
                      <p className="text-sm leading-5 text-muted">
                        {t(currentItem.subtitle)}
                      </p>
                    </header>
                  )}
                  <Outlet />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
