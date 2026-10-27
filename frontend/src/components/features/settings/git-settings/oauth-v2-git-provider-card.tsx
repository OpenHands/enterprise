import React from "react";
import { useTranslation } from "react-i18next";
import { useConfig } from "#/hooks/query/use-config";
import { useOAuthV2GitProviderCheck } from "#/hooks/query/use-oauth-v2-git-provider-check";
import type { OAuthV2GitProvider } from "#/api/oauth-v2-service/oauth-v2-service.api";
import { I18nKey } from "#/i18n/declaration";
import type { Provider } from "#/types/settings";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { settingsListRowActionButtonClassName } from "#/utils/settings-list-classes";
import { BrandButton } from "../brand-button";
import { ConfigureGitHubRepositoriesAnchor } from "./configure-github-repositories-anchor";
import { GitLabWebhookManager } from "./gitlab-webhook-manager";
import { BitbucketDCWebhookManager } from "./bitbucket-dc-webhook-manager";
import { AzureDevOpsWebhookManager } from "./azure-devops-webhook-manager";
import { GitProviderConnection } from "./git-provider-connection";
import { IntegrationProviderCard } from "./integration-provider-card";
import type { IntegrationProviderId } from "./integration-provider-icon";

const STATUS_TEST_IDS: Partial<Record<Provider, string>> = {
  bitbucket_data_center: "bitbucket-dc-status-text",
  azure_devops: "azure-devops-status-text",
};

interface OAuthV2GitProviderCardProps {
  row: OAuthV2GitProvider;
}

/**
 * Integrations row for a git provider defined in `auth_providers`. A linked
 * provider's token is tested against the provider when the card mounts and
 * again on "Test connection"; a rejected token turns the card into a
 * Reconnect prompt.
 */
export function OAuthV2GitProviderCard({ row }: OAuthV2GitProviderCardProps) {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const provider = row.provider_type as Provider;
  const {
    data: check,
    refetch: recheck,
    isFetching: isChecking,
  } = useOAuthV2GitProviderCheck(row.id, row.connected);
  const needsReconnect =
    row.connected && (check?.needs_reconnect ?? row.needs_reconnect);
  const isHealthy = row.connected && !needsReconnect;

  const handleTestConnection = async () => {
    const { data } = await recheck();
    const options = { provider: row.title };
    if (data?.status === "valid") {
      displaySuccessToast(t(I18nKey.GIT_PROVIDER$CONNECTION_VALID, options));
    } else if (data?.status === "invalid") {
      displayErrorToast(t(I18nKey.GIT_PROVIDER$CONNECTION_INVALID, options));
    } else {
      displayErrorToast(
        t(I18nKey.GIT_PROVIDER$CONNECTION_UNAVAILABLE, options),
      );
    }
  };

  let statusLabel = t(I18nKey.STATUS$NOT_CONNECTED);
  if (needsReconnect) {
    statusLabel = t(I18nKey.STATUS$RECONNECT_REQUIRED);
  } else if (row.connected) {
    statusLabel = t(I18nKey.STATUS$CONNECTED);
  }

  // A single node (not several `cond && <X />` siblings): the card renders its
  // bordered children panel whenever `children` is truthy, and an array of
  // nulls is truthy.
  const webhookManagers: Partial<Record<Provider, React.ReactNode>> = {
    gitlab: <GitLabWebhookManager />,
    bitbucket_data_center: <BitbucketDCWebhookManager />,
    azure_devops: <AzureDevOpsWebhookManager />,
  };

  return (
    <IntegrationProviderCard
      provider={provider as IntegrationProviderId}
      title={row.title}
      isConnected={isHealthy}
      statusTestId={STATUS_TEST_IDS[provider] ?? `${provider}-status-text`}
      statusLabel={statusLabel}
      action={
        <GitProviderConnection
          provider={provider}
          providerName={row.title}
          isConnected={row.connected}
          needsReconnect={needsReconnect}
          oauthProviderId={row.id}
        >
          {provider === "github" && config?.github_app_slug ? (
            <ConfigureGitHubRepositoriesAnchor
              slug={config.github_app_slug}
              isInstalled
            />
          ) : null}
          <BrandButton
            testId={`test-${provider}-connection-button`}
            type="button"
            variant="secondary"
            className={settingsListRowActionButtonClassName}
            isDisabled={isChecking}
            onClick={handleTestConnection}
          >
            {t(I18nKey.BUTTON$TEST_CONNECTION)}
          </BrandButton>
        </GitProviderConnection>
      }
    >
      {isHealthy ? webhookManagers[provider] : null}
    </IntegrationProviderCard>
  );
}
