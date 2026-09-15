import type { ComponentType } from "react";
import { SiForgejo, SiJira, SiLinear } from "react-icons/si";
import GitHubLogo from "#/assets/branding/github-logo.svg?react";
import GitLabLogo from "#/assets/branding/gitlab-logo.svg?react";
import BitbucketLogo from "#/assets/branding/bitbucket-logo.svg?react";
import AzureDevOpsLogo from "#/assets/branding/azure-devops-logo.svg?react";
import SlackLogo from "#/icons/slack.svg?react";
import { getHubCatalogLogoBranding } from "#/components/features/integrations-hub/hub-catalog-logo-branding";
import { HubCatalogGlyph } from "#/components/features/integrations-hub/hub-catalog-logos";
import { cn } from "#/utils/utils";

export const INTEGRATION_PROVIDER_IDS = [
  "github",
  "gitlab",
  "bitbucket",
  "bitbucket_data_center",
  "azure_devops",
  "forgejo",
  "slack",
  "jira",
  "jira-dc",
  "linear",
] as const;

export type IntegrationProviderId = (typeof INTEGRATION_PROVIDER_IDS)[number];

export function isIntegrationProviderId(
  provider: string,
): provider is IntegrationProviderId {
  return (INTEGRATION_PROVIDER_IDS as readonly string[]).includes(provider);
}

interface IntegrationProviderIconProps {
  /** Known git/PM providers, or any Hub slug (falls back to an initial). */
  provider: string;
  /** Optional remote catalog mark when no bundled icon exists. */
  logoUrl?: string;
  /** Optional Hub catalog tile colors. Falls back to brand map by slug. */
  iconBg?: string;
  iconColor?: string;
  className?: string;
  /** Visual size of the badge shell. */
  size?: "sm" | "md";
}

const SIZE_CLASS: Record<
  NonNullable<IntegrationProviderIconProps["size"]>,
  string
> = {
  sm: "size-7 rounded-md [&>svg]:size-3.5",
  md: "size-9 rounded-lg [&>svg]:size-4",
};

function SimpleIcon({
  Icon,
}: {
  Icon: ComponentType<{ className?: string; size?: number }>;
}) {
  return <Icon className="shrink-0" size={16} />;
}

function ProviderGlyph({ provider }: { provider: IntegrationProviderId }) {
  switch (provider) {
    case "github":
      return <GitHubLogo aria-hidden className="shrink-0" />;
    case "gitlab":
      return <GitLabLogo aria-hidden className="shrink-0" />;
    case "bitbucket":
    case "bitbucket_data_center":
      return <BitbucketLogo aria-hidden className="shrink-0" />;
    case "azure_devops":
      return <AzureDevOpsLogo aria-hidden className="shrink-0" />;
    case "slack":
      return <SlackLogo aria-hidden className="shrink-0" />;
    case "jira":
    case "jira-dc":
      return <SimpleIcon Icon={SiJira} />;
    case "linear":
      return <SimpleIcon Icon={SiLinear} />;
    case "forgejo":
      return <SimpleIcon Icon={SiForgejo} />;
    default:
      return null;
  }
}

/**
 * Brand mark for an integration provider, rendered inside a Hub brand tile.
 */
export function IntegrationProviderIcon({
  provider,
  logoUrl,
  iconBg,
  iconColor,
  className,
  size = "md",
}: IntegrationProviderIconProps) {
  const knownProvider = isIntegrationProviderId(provider)
    ? provider
    : undefined;
  const initial = provider.trim().charAt(0).toUpperCase() || "?";
  const fallback = <span className="text-xs font-semibold">{initial}</span>;
  const branding = getHubCatalogLogoBranding(provider, { iconBg, iconColor });

  return (
    <span
      aria-hidden="true"
      data-testid={`integration-provider-icon-${provider}`}
      className={cn(
        "inline-flex shrink-0 items-center justify-center overflow-hidden border border-white/10 shadow-[inset_0_1px_0_rgba(255,255,255,0.18)]",
        SIZE_CLASS[size],
        className,
      )}
      style={{
        backgroundColor: branding.iconBg,
        color: branding.iconColor,
      }}
    >
      {knownProvider ? (
        <ProviderGlyph provider={knownProvider} />
      ) : (
        <HubCatalogGlyph
          slug={provider}
          logoUrl={logoUrl}
          fallback={fallback}
        />
      )}
    </span>
  );
}
