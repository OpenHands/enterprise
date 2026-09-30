import type { IntegrationProviderId } from "#/components/features/settings/git-settings/integration-provider-icon";
import { INTEGRATION_PROVIDER_IDS } from "#/components/features/settings/git-settings/integration-provider-icon";
import { I18nKey } from "#/i18n/declaration";

export interface LegacyResolverDefinition {
  id: IntegrationProviderId;
  nameKey: I18nKey;
  sublineKey: I18nKey;
  path: string;
}

const RESOLVER_NAME_KEYS: Record<IntegrationProviderId, I18nKey> = {
  github: I18nKey.SETTINGS$RESOLVER_GITHUB,
  gitlab: I18nKey.SETTINGS$RESOLVER_GITLAB,
  bitbucket: I18nKey.SETTINGS$RESOLVER_BITBUCKET,
  bitbucket_data_center: I18nKey.SETTINGS$RESOLVER_BITBUCKET_DC,
  azure_devops: I18nKey.SETTINGS$RESOLVER_AZURE_DEVOPS,
  forgejo: I18nKey.SETTINGS$RESOLVER_FORGEJO,
  slack: I18nKey.SETTINGS$RESOLVER_SLACK,
  jira: I18nKey.SETTINGS$RESOLVER_JIRA,
  "jira-dc": I18nKey.SETTINGS$RESOLVER_JIRA_DC,
  linear: I18nKey.SETTINGS$RESOLVER_LINEAR,
};

const RESOLVER_SUBLINE_KEYS: Record<IntegrationProviderId, I18nKey> = {
  github: I18nKey.SETTINGS$RESOLVER_GITHUB_SUBLINE,
  gitlab: I18nKey.SETTINGS$RESOLVER_GITLAB_SUBLINE,
  bitbucket: I18nKey.SETTINGS$RESOLVER_BITBUCKET_SUBLINE,
  bitbucket_data_center: I18nKey.SETTINGS$RESOLVER_BITBUCKET_DC_SUBLINE,
  azure_devops: I18nKey.SETTINGS$RESOLVER_AZURE_DEVOPS_SUBLINE,
  forgejo: I18nKey.SETTINGS$RESOLVER_FORGEJO_SUBLINE,
  slack: I18nKey.SETTINGS$RESOLVER_SLACK_SUBLINE,
  jira: I18nKey.SETTINGS$RESOLVER_JIRA_SUBLINE,
  "jira-dc": I18nKey.SETTINGS$RESOLVER_JIRA_DC_SUBLINE,
  linear: I18nKey.SETTINGS$RESOLVER_LINEAR_SUBLINE,
};

/** Git and project-management providers shown on personal Integrations. */
export const LEGACY_RESOLVERS: LegacyResolverDefinition[] =
  INTEGRATION_PROVIDER_IDS.map((id) => ({
    id,
    nameKey: RESOLVER_NAME_KEYS[id],
    sublineKey: RESOLVER_SUBLINE_KEYS[id],
    path: "/settings/integrations",
  }));

export function isLegacyResolverId(
  value: string | undefined,
): value is IntegrationProviderId {
  return (
    !!value && (INTEGRATION_PROVIDER_IDS as readonly string[]).includes(value)
  );
}

export function getLegacyResolver(
  id: string | undefined,
): LegacyResolverDefinition | undefined {
  if (!isLegacyResolverId(id)) {
    return undefined;
  }
  return LEGACY_RESOLVERS.find((resolver) => resolver.id === id);
}
