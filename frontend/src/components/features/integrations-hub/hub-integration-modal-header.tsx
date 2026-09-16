import { HubTruncatedText } from "#/components/features/integrations-hub/hub-truncated-text";
import { IntegrationProviderIcon } from "#/components/features/settings/git-settings/integration-provider-icon";
import type { HubIntegration } from "#/types/integrations-hub";

interface HubIntegrationModalHeaderProps {
  integration: HubIntegration;
  showDescription?: boolean;
  testId?: string;
}

export function HubIntegrationModalHeader({
  integration,
  showDescription = false,
  testId,
}: HubIntegrationModalHeaderProps) {
  return (
    <header className="flex items-start gap-3" data-testid={testId}>
      <IntegrationProviderIcon
        provider={integration.slug}
        logoUrl={integration.logoUrl}
        size="md"
      />
      <div className="min-w-0 flex-1">
        <h2 className="min-w-0 pr-6 text-lg font-semibold leading-5 text-white">
          <HubTruncatedText text={integration.name} />
        </h2>
        {showDescription && integration.description ? (
          <p className="mt-0.5 text-sm leading-5 text-tertiary-light">
            {integration.description}
          </p>
        ) : null}
      </div>
    </header>
  );
}
