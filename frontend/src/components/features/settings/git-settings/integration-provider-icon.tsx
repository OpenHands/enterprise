import type { ComponentType } from "react";
import { SiForgejo, SiJira, SiLinear } from "react-icons/si";
import GitHubLogo from "#/assets/branding/github-logo.svg?react";
import GitLabLogo from "#/assets/branding/gitlab-logo.svg?react";
import BitbucketLogo from "#/assets/branding/bitbucket-logo.svg?react";
import AzureDevOpsLogo from "#/assets/branding/azure-devops-logo.svg?react";
import SlackLogo from "#/icons/slack.svg?react";
import { cn } from "#/utils/utils";

export type IntegrationProviderId =
  | "github"
  | "gitlab"
  | "bitbucket"
  | "bitbucket_data_center"
  | "azure_devops"
  | "forgejo"
  | "slack"
  | "jira"
  | "jira-dc"
  | "linear";

interface IntegrationProviderIconProps {
  provider: IntegrationProviderId;
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

/** Shared shell matches GitHub: neutral `bg-white/10` badge. Brand color stays on the glyph. */
const PROVIDER_SHELL = "bg-white/10";

const PROVIDER_SURFACE: Record<IntegrationProviderId, string> = {
  github: `${PROVIDER_SHELL} text-white`,
  gitlab: `${PROVIDER_SHELL} text-[#FC6B0E]`,
  bitbucket: `${PROVIDER_SHELL} text-[#2684FF]`,
  bitbucket_data_center: `${PROVIDER_SHELL} text-[#2684FF]`,
  azure_devops: `${PROVIDER_SHELL} text-[#0078D4]`,
  forgejo: `${PROVIDER_SHELL} text-orange-400`,
  // Multicolor Slack SVG — no currentColor tint needed.
  slack: PROVIDER_SHELL,
  jira: `${PROVIDER_SHELL} text-[#2684FF]`,
  "jira-dc": `${PROVIDER_SHELL} text-[#2684FF]`,
  linear: `${PROVIDER_SHELL} text-white`,
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
 * Brand mark for an integration provider, rendered inside a neutral badge.
 */
export function IntegrationProviderIcon({
  provider,
  className,
  size = "md",
}: IntegrationProviderIconProps) {
  return (
    <span
      aria-hidden="true"
      data-testid={`integration-provider-icon-${provider}`}
      className={cn(
        "inline-flex shrink-0 items-center justify-center border border-white/10",
        SIZE_CLASS[size],
        PROVIDER_SURFACE[provider],
        className,
      )}
    >
      <ProviderGlyph provider={provider} />
    </span>
  );
}
