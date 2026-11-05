import type { ComponentType } from "react";
import { Plug } from "lucide-react";
import { SiBrave, SiNotion, SiSentry } from "react-icons/si";
import {
  IntegrationProviderIcon,
  type IntegrationProviderId,
} from "#/components/features/settings/git-settings/integration-provider-icon";

interface MCPServerIdentity {
  name?: string;
  url?: string;
  command?: string;
  args?: string[];
}

const BADGE_CLASS_NAME =
  "inline-flex size-9 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/10";

const PROVIDER_BY_TOKEN: { token: string; provider: IntegrationProviderId }[] =
  [
    { token: "bitbucket-data-center", provider: "bitbucket_data_center" },
    { token: "bitbucket_data_center", provider: "bitbucket_data_center" },
    { token: "azure-devops", provider: "azure_devops" },
    { token: "azure_devops", provider: "azure_devops" },
    { token: "jira-dc", provider: "jira-dc" },
    { token: "bitbucket", provider: "bitbucket" },
    { token: "forgejo", provider: "forgejo" },
    { token: "github", provider: "github" },
    { token: "gitlab", provider: "gitlab" },
    { token: "linear", provider: "linear" },
    { token: "slack", provider: "slack" },
    { token: "jira", provider: "jira" },
  ];

const SIMPLE_ICONS: {
  token: string;
  id: string;
  Icon: ComponentType<{ className?: string; size?: number }>;
  className: string;
}[] = [
  {
    token: "brave",
    id: "brave",
    Icon: SiBrave,
    className: "text-[#FB542B]",
  },
  {
    token: "notion",
    id: "notion",
    Icon: SiNotion,
    className: "text-white",
  },
  {
    token: "sentry",
    id: "sentry",
    Icon: SiSentry,
    className: "text-white",
  },
];

function identityText(server: MCPServerIdentity): string {
  return [server.name, server.url, server.command, ...(server.args ?? [])]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export function MCPServerIcon({ server }: { server: MCPServerIdentity }) {
  const identity = identityText(server);
  const provider = PROVIDER_BY_TOKEN.find(({ token }) =>
    identity.includes(token),
  )?.provider;

  if (provider) {
    return <IntegrationProviderIcon provider={provider} size="md" />;
  }

  const simple = SIMPLE_ICONS.find(({ token }) => identity.includes(token));
  if (simple) {
    return (
      <span
        aria-hidden
        data-testid={`mcp-server-icon-${simple.id}`}
        className={BADGE_CLASS_NAME}
      >
        <simple.Icon className={simple.className} size={16} />
      </span>
    );
  }

  return (
    <span aria-hidden className={`${BADGE_CLASS_NAME} text-white`}>
      <Plug className="size-4" strokeWidth={1.75} />
    </span>
  );
}
