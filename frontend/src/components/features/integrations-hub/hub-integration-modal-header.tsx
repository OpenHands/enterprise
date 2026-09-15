import { useTranslation } from "react-i18next";
import { hubCardPillClassName } from "#/components/features/integrations-hub/hub-card-classes";
import {
  hubAuthLabel,
  hubIntegrationMetaPills,
} from "#/components/features/integrations-hub/hub-format";
import { HubTruncatedText } from "#/components/features/integrations-hub/hub-truncated-text";
import { IntegrationProviderIcon } from "#/components/features/settings/git-settings/integration-provider-icon";
import { I18nKey } from "#/i18n/declaration";
import type { HubIntegration } from "#/types/integrations-hub";

interface HubIntegrationModalHeaderProps {
  integration: HubIntegration;
  hideDisabledTools?: boolean;
  showDescription?: boolean;
  testId?: string;
}

export function HubIntegrationModalHeader({
  integration,
  hideDisabledTools = false,
  showDescription = false,
  testId,
}: HubIntegrationModalHeaderProps) {
  const { t } = useTranslation();
  const authLabel = hubAuthLabel(integration.authStrategy, t);
  const pills = hubIntegrationMetaPills(integration);
  const visibleToolCount = hideDisabledTools
    ? integration.tools.filter((tool) => tool.accessMode !== "disabled").length
    : integration.tools.length || integration.toolCount;

  return (
    <header className="flex items-start gap-3 pr-6" data-testid={testId}>
      <IntegrationProviderIcon
        provider={integration.slug}
        logoUrl={integration.logoUrl}
        size="md"
      />
      <div className="min-w-0 flex-1">
        <h2 className="min-w-0 text-base font-semibold text-white">
          <HubTruncatedText text={integration.name} />
        </h2>
        {authLabel ? (
          <p className="mt-0.5 text-xs text-tertiary-alt">{authLabel}</p>
        ) : null}
        {showDescription && integration.description ? (
          <p className="mt-2 text-sm leading-6 text-tertiary-light">
            {integration.description}
          </p>
        ) : null}
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className={hubCardPillClassName}>
            {t(I18nKey.INTEGRATIONS_HUB$TOOLS_COUNT, {
              count: visibleToolCount,
            })}
          </span>
          {pills.map((label) => (
            <span key={label} className={hubCardPillClassName}>
              {label}
            </span>
          ))}
        </div>
      </div>
    </header>
  );
}
