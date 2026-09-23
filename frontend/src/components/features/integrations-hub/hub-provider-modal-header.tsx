import { IntegrationProviderIcon } from "#/components/features/settings/git-settings/integration-provider-icon";
import { cn } from "#/utils/utils";

interface HubProviderModalHeaderProps {
  provider: string;
  title: string;
  subtitle?: string;
  logoUrl?: string;
  className?: string;
  testId?: string;
}

/** Icon + title/subline header used by Integrations Hub resolver modals. */
export function HubProviderModalHeader({
  provider,
  title,
  subtitle,
  logoUrl,
  className,
  testId,
}: HubProviderModalHeaderProps) {
  return (
    <header
      className={cn("flex items-start gap-3 pr-8", className)}
      data-testid={testId}
    >
      <IntegrationProviderIcon
        provider={provider}
        logoUrl={logoUrl}
        size="md"
      />
      <div className="min-w-0 flex-1 space-y-1">
        <h2 className="text-lg font-semibold leading-5 text-white">{title}</h2>
        {subtitle ? (
          <p className="text-sm leading-5 text-tertiary-light">{subtitle}</p>
        ) : null}
      </div>
    </header>
  );
}
