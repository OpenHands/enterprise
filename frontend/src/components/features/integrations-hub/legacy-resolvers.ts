import type { IntegrationProviderId } from "#/components/features/settings/git-settings/integration-provider-icon";
import { INTEGRATION_PROVIDER_IDS } from "#/components/features/settings/git-settings/integration-provider-icon";
import { I18nKey } from "#/i18n/declaration";
import { INTEGRATIONS_HUB_PATHS } from "./integrations-hub-paths";

export interface LegacyResolverDefinition {
  id: IntegrationProviderId;
  nameKey: I18nKey;
  sublineKey: I18nKey;
  path: string;
}

const RESOLVER_NAME_KEYS: Record<IntegrationProviderId, I18nKey> = {
  github: I18nKey.INTEGRATIONS_HUB$RESOLVER_GITHUB,
  gitlab: I18nKey.INTEGRATIONS_HUB$RESOLVER_GITLAB,
  bitbucket: I18nKey.INTEGRATIONS_HUB$RESOLVER_BITBUCKET,
  bitbucket_data_center: I18nKey.INTEGRATIONS_HUB$RESOLVER_BITBUCKET_DC,
  azure_devops: I18nKey.INTEGRATIONS_HUB$RESOLVER_AZURE_DEVOPS,
  forgejo: I18nKey.INTEGRATIONS_HUB$RESOLVER_FORGEJO,
  slack: I18nKey.INTEGRATIONS_HUB$RESOLVER_SLACK,
  jira: I18nKey.INTEGRATIONS_HUB$RESOLVER_JIRA,
  "jira-dc": I18nKey.INTEGRATIONS_HUB$RESOLVER_JIRA_DC,
  linear: I18nKey.INTEGRATIONS_HUB$RESOLVER_LINEAR,
};

const RESOLVER_SUBLINE_KEYS: Record<IntegrationProviderId, I18nKey> = {
  github: I18nKey.INTEGRATIONS_HUB$RESOLVER_GITHUB_SUBLINE,
  gitlab: I18nKey.INTEGRATIONS_HUB$RESOLVER_GITLAB_SUBLINE,
  bitbucket: I18nKey.INTEGRATIONS_HUB$RESOLVER_BITBUCKET_SUBLINE,
  bitbucket_data_center: I18nKey.INTEGRATIONS_HUB$RESOLVER_BITBUCKET_DC_SUBLINE,
  azure_devops: I18nKey.INTEGRATIONS_HUB$RESOLVER_AZURE_DEVOPS_SUBLINE,
  forgejo: I18nKey.INTEGRATIONS_HUB$RESOLVER_FORGEJO_SUBLINE,
  slack: I18nKey.INTEGRATIONS_HUB$RESOLVER_SLACK_SUBLINE,
  jira: I18nKey.INTEGRATIONS_HUB$RESOLVER_JIRA_SUBLINE,
  "jira-dc": I18nKey.INTEGRATIONS_HUB$RESOLVER_JIRA_DC_SUBLINE,
  linear: I18nKey.INTEGRATIONS_HUB$RESOLVER_LINEAR_SUBLINE,
};

/** Legacy Settings / resolver providers surfaced under Integrations Admin. */
export const LEGACY_RESOLVERS: LegacyResolverDefinition[] =
  INTEGRATION_PROVIDER_IDS.map((id) => ({
    id,
    nameKey: RESOLVER_NAME_KEYS[id],
    sublineKey: RESOLVER_SUBLINE_KEYS[id],
    path: `${INTEGRATIONS_HUB_PATHS.resolvers}/${id}`,
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
