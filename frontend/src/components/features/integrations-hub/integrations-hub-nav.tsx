import { useState, type ReactNode } from "react";
import {
  Blocks,
  ChevronDown,
  CircleCheck,
  Grid2x2,
  Inbox,
  KeyRound,
  Target,
  Webhook,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";
import { HubTruncatedText } from "#/components/features/integrations-hub/hub-truncated-text";
import { SettingsNavLink } from "#/components/features/settings/settings-nav-link";
import {
  SIDEBAR_ICON_SLOT_CLASS,
  SIDEBAR_NAV_COUNT_CLASS,
} from "#/components/features/sidebar/sidebar-layout";
import { SettingsNavItem } from "#/constants/settings-nav";
import { useBreakpoint } from "#/hooks/use-breakpoint";
import { useClickOutsideElement } from "#/hooks/use-click-outside-element";
import { I18nKey } from "#/i18n/declaration";
import { formControlFieldClassName } from "#/utils/form-control-classes";
import {
  dropdownMenuListClassName,
  dropdownMenuPanelPaddingClassName,
} from "#/utils/dropdown-classes";
import { cn } from "#/utils/utils";
import {
  INTEGRATIONS_HUB_PATHS,
  PERSONAL_INTEGRATIONS_PATHS,
} from "./integrations-hub-paths";

const ICON = 18;
const MOBILE_NAV_BREAKPOINT = 767;

const PERSONAL_ITEMS: SettingsNavItem[] = [
  {
    icon: <Blocks size={ICON} aria-hidden />,
    to: PERSONAL_INTEGRATIONS_PATHS.integrations,
    text: I18nKey.SETTINGS$NAV_INTEGRATIONS,
    subtitle: I18nKey.INTEGRATIONS_HUB$PAGE_INTEGRATIONS_SUBLINE,
    end: true,
  },
  {
    icon: <CircleCheck size={ICON} aria-hidden />,
    to: PERSONAL_INTEGRATIONS_PATHS.agentRequests,
    text: I18nKey.INTEGRATIONS_HUB$NAV_AGENT_REQUESTS,
    subtitle: I18nKey.INTEGRATIONS_HUB$PAGE_AGENT_REQUESTS_SUBLINE,
  },
  {
    icon: <KeyRound size={ICON} aria-hidden />,
    to: PERSONAL_INTEGRATIONS_PATHS.agentConnection,
    text: I18nKey.INTEGRATIONS_HUB$NAV_AGENT_CONNECTION,
    subtitle: I18nKey.INTEGRATIONS_HUB$PAGE_AGENT_CONNECTION_SUBLINE,
  },
];

const ADMIN_ITEMS: SettingsNavItem[] = [
  {
    icon: <Grid2x2 size={ICON} aria-hidden />,
    to: INTEGRATIONS_HUB_PATHS.adminCatalog,
    text: I18nKey.INTEGRATIONS_HUB$NAV_ADMIN_CATALOG,
    subtitle: I18nKey.INTEGRATIONS_HUB$PAGE_ADMIN_CATALOG_SUBLINE,
  },
  {
    icon: <Target size={ICON} aria-hidden />,
    to: INTEGRATIONS_HUB_PATHS.adminOverview,
    text: I18nKey.INTEGRATIONS_HUB$NAV_ADMIN_OVERVIEW,
    subtitle: I18nKey.INTEGRATIONS_HUB$PAGE_ADMIN_OVERVIEW_SUBLINE,
  },
  {
    icon: <Inbox size={ICON} aria-hidden />,
    to: INTEGRATIONS_HUB_PATHS.adminUserRequests,
    text: I18nKey.INTEGRATIONS_HUB$NAV_ADMIN_USER_REQUESTS,
    subtitle: I18nKey.INTEGRATIONS_HUB$PAGE_ADMIN_USER_REQUESTS_SUBLINE,
  },
  {
    icon: <Webhook size={ICON} aria-hidden />,
    to: INTEGRATIONS_HUB_PATHS.resolvers,
    text: I18nKey.INTEGRATIONS_HUB$NAV_RESOLVERS_LEGACY,
    subtitle: I18nKey.INTEGRATIONS_HUB$PAGE_RESOLVERS_SUBLINE,
    end: false,
  },
];

function NavCount({ count }: { count: number }) {
  return (
    <span
      aria-hidden
      data-testid="dashboard-nav-trailing-count"
      className={SIDEBAR_NAV_COUNT_CLASS}
    >
      {count}
    </span>
  );
}

function itemTrailingCount(
  item: SettingsNavItem,
  isPersonal: boolean,
  agentRequestCount: number,
  userRequestCount: number,
): number {
  if (isPersonal && item.to === PERSONAL_INTEGRATIONS_PATHS.agentRequests) {
    return agentRequestCount;
  }
  if (!isPersonal && item.to === INTEGRATIONS_HUB_PATHS.adminUserRequests) {
    return userRequestCount;
  }
  return 0;
}

function navItemTrailing(count: number): ReactNode {
  return count > 0 ? <NavCount count={count} /> : undefined;
}

function HubNavLinks({
  items,
  isPersonal,
  agentRequestCount,
  userRequestCount,
  ariaLabel,
  onItemClick,
}: {
  items: SettingsNavItem[];
  isPersonal: boolean;
  agentRequestCount: number;
  userRequestCount: number;
  ariaLabel: string;
  onItemClick?: () => void;
}) {
  return (
    <nav
      className="flex w-full shrink-0 flex-col items-stretch gap-0.5"
      aria-label={ariaLabel}
    >
      {items.map((item) => (
        <SettingsNavLink
          key={item.to}
          item={item}
          onClick={onItemClick}
          trailing={navItemTrailing(
            itemTrailingCount(
              item,
              isPersonal,
              agentRequestCount,
              userRequestCount,
            ),
          )}
        />
      ))}
    </nav>
  );
}

function HubNavMobileDropdown({
  items,
  currentItem,
  isPersonal,
  agentRequestCount,
  userRequestCount,
  ariaLabel,
}: {
  items: SettingsNavItem[];
  currentItem: SettingsNavItem;
  isPersonal: boolean;
  agentRequestCount: number;
  userRequestCount: number;
  ariaLabel: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const menuRef = useClickOutsideElement<HTMLDivElement>(() => setOpen(false));
  const currentCount = itemTrailingCount(
    currentItem,
    isPersonal,
    agentRequestCount,
    userRequestCount,
  );

  return (
    <div
      ref={menuRef}
      className="relative w-full"
      data-testid="integrations-hub-nav-mobile"
    >
      <button
        type="button"
        data-testid="integrations-hub-nav-mobile-trigger"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className={cn(
          formControlFieldClassName,
          "flex w-full cursor-pointer items-center gap-2 px-3 text-left",
        )}
      >
        <span className={SIDEBAR_ICON_SLOT_CLASS}>{currentItem.icon}</span>
        <HubTruncatedText
          text={t(currentItem.text as I18nKey)}
          className="flex-1 text-sm text-white"
        />
        {navItemTrailing(currentCount)}
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-tertiary-alt",
            open && "rotate-180",
          )}
          aria-hidden
        />
      </button>
      {open ? (
        <div
          role="listbox"
          data-testid="integrations-hub-nav-mobile-menu"
          className={cn(
            "absolute left-0 right-0 top-full z-50 mt-1",
            "rounded-[6px] bg-tertiary context-menu-box-shadow",
            dropdownMenuPanelPaddingClassName,
            dropdownMenuListClassName,
          )}
        >
          <HubNavLinks
            items={items}
            isPersonal={isPersonal}
            agentRequestCount={agentRequestCount}
            userRequestCount={userRequestCount}
            ariaLabel={ariaLabel}
            onItemClick={() => setOpen(false)}
          />
        </div>
      ) : null}
    </div>
  );
}

interface IntegrationsHubNavProps {
  variant: "personal" | "admin";
  agentRequestCount?: number;
  userRequestCount?: number;
}

export function IntegrationsHubNav({
  variant,
  agentRequestCount = 0,
  userRequestCount = 0,
}: IntegrationsHubNavProps) {
  const { t } = useTranslation();
  const location = useLocation();
  const isMobile = useBreakpoint(MOBILE_NAV_BREAKPOINT);
  const isPersonal = variant === "personal";
  const items = isPersonal ? PERSONAL_ITEMS : ADMIN_ITEMS;
  const currentItem =
    items.find((item) =>
      item.end === false
        ? location.pathname === item.to ||
          location.pathname.startsWith(`${item.to}/`)
        : location.pathname === item.to,
    ) ?? items[0]!;
  const ariaLabel = t(
    isPersonal
      ? I18nKey.INTEGRATIONS_HUB$NAV_MAIN
      : I18nKey.INTEGRATIONS_HUB$NAV_ADMINISTRATION,
  );

  return (
    <aside
      data-testid={
        isPersonal ? "personal-integrations-navbar" : "integrations-hub-navbar"
      }
      className="flex w-full shrink-0 flex-col gap-2 md:sticky md:top-8 md:w-[260px] md:self-start"
    >
      {isMobile ? (
        <HubNavMobileDropdown
          items={items}
          currentItem={currentItem}
          isPersonal={isPersonal}
          agentRequestCount={agentRequestCount}
          userRequestCount={userRequestCount}
          ariaLabel={ariaLabel}
        />
      ) : (
        <>
          <span
            data-testid="integrations-hub-nav-title"
            className="px-2 text-sm font-normal text-white"
          >
            {t(
              isPersonal
                ? I18nKey.SETTINGS$NAV_INTEGRATIONS
                : I18nKey.SETTINGS$NAV_INTEGRATIONS_HUB,
            )}
          </span>
          <div className="flex flex-col gap-0.5 pt-0.5">
            <HubNavLinks
              items={items}
              isPersonal={isPersonal}
              agentRequestCount={agentRequestCount}
              userRequestCount={userRequestCount}
              ariaLabel={ariaLabel}
            />
          </div>
        </>
      )}
    </aside>
  );
}
