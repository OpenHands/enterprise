import { Navigate, Outlet, useLocation } from "react-router";
import { useTranslation } from "react-i18next";
import { useConfig } from "#/hooks/query/use-config";
import {
  IntegrationsHubStubProvider,
  useIntegrationsHubStub,
} from "#/hooks/query/use-integrations-hub-stub";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";
import { IntegrationsHubNav } from "./integrations-hub-nav";
import {
  PERSONAL_INTEGRATIONS_PATHS,
  isPersonalIntegrationsPath,
} from "./integrations-hub-paths";

export const handle = { hideTitle: true, wideContent: true };

function PersonalIntegrationsLegacyHeader() {
  const { t } = useTranslation();

  return (
    <header className="space-y-1">
      <Typography.H2>{t(I18nKey.SETTINGS$NAV_INTEGRATIONS)}</Typography.H2>
      <p
        data-testid="settings-page-subtitle"
        className="text-sm leading-5 text-muted"
      >
        {t(I18nKey.SETTINGS$PAGE_INTEGRATIONS_SUBLINE)}
      </p>
    </header>
  );
}

function PersonalIntegrationsHubInner() {
  const hub = useIntegrationsHubStub();

  return (
    <div
      className="flex min-h-0 flex-col gap-4 md:flex-row md:gap-6 lg:gap-10"
      data-testid="personal-integrations-layout"
    >
      <IntegrationsHubNav
        variant="personal"
        agentRequestCount={
          hub.approvals.filter((item) => item.status === "pending").length
        }
      />
      <div className="mx-auto flex w-full min-w-0 max-w-[800px] flex-1 flex-col gap-6">
        <Outlet />
      </div>
    </div>
  );
}

function PersonalIntegrationsLayoutInner() {
  const location = useLocation();
  const { data: config } = useConfig();
  const hubEnabled = config?.feature_flags?.enable_integrations_hub === true;
  const isNestedPersonalPage =
    isPersonalIntegrationsPath(location.pathname) &&
    location.pathname !== PERSONAL_INTEGRATIONS_PATHS.integrations;

  if (!hubEnabled && isNestedPersonalPage) {
    return <Navigate to={PERSONAL_INTEGRATIONS_PATHS.integrations} replace />;
  }

  if (!hubEnabled) {
    return (
      <>
        <PersonalIntegrationsLegacyHeader />
        <Outlet />
      </>
    );
  }

  return (
    <IntegrationsHubStubProvider>
      <PersonalIntegrationsHubInner />
    </IntegrationsHubStubProvider>
  );
}

export function PersonalIntegrationsLayout() {
  return <PersonalIntegrationsLayoutInner />;
}
