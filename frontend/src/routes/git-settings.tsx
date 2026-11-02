import React from "react";
import { Link } from "react-router";
import { Trans, useTranslation } from "react-i18next";
import { useConfig } from "#/hooks/query/use-config";
import { useMe } from "#/hooks/query/use-me";
import { createPermissionGuard } from "#/utils/org/permission-guard";
import { useSettings } from "#/hooks/query/use-settings";
import { BrandButton } from "#/components/features/settings/brand-button";
import { useDeleteGitProviders } from "#/hooks/mutation/use-delete-git-providers";
import { GitHubTokenInput } from "#/components/features/settings/git-settings/github-token-input";
import { GitLabTokenInput } from "#/components/features/settings/git-settings/gitlab-token-input";
import { GitLabWebhookManager } from "#/components/features/settings/git-settings/gitlab-webhook-manager";
import { BitbucketTokenInput } from "#/components/features/settings/git-settings/bitbucket-token-input";
import { BitbucketDCTokenInput } from "#/components/features/settings/git-settings/bitbucket-dc-token-help-input";
import { BitbucketDCWebhookManager } from "#/components/features/settings/git-settings/bitbucket-dc-webhook-manager";
import { AzureDevOpsTokenInput } from "#/components/features/settings/git-settings/azure-devops-token-input";
import { AzureDevOpsWebhookManager } from "#/components/features/settings/git-settings/azure-devops-webhook-manager";
import { ForgejoTokenInput } from "#/components/features/settings/git-settings/forgejo-token-input";
import { ConfigureGitHubRepositoriesAnchor } from "#/components/features/settings/git-settings/configure-github-repositories-anchor";
import { GitProviderConnection } from "#/components/features/settings/git-settings/git-provider-connection";
import { ConfigureAzureDevOpsAnchor } from "#/components/features/settings/git-settings/configure-azure-devops-anchor";
import { InstallSlackAppAnchor } from "#/components/features/settings/git-settings/install-slack-app-anchor";
import { IntegrationProviderCard } from "#/components/features/settings/git-settings/integration-provider-card";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { GitSettingInputsSkeleton } from "#/components/features/settings/git-settings/github-settings-inputs-skeleton";
import { useAddGitProviders } from "#/hooks/mutation/use-add-git-providers";
import { useUserProviders } from "#/hooks/use-user-providers";
import { useSlackStatus } from "#/hooks/query/use-slack-status";
import { useOAuthV2GitProviders } from "#/hooks/query/use-oauth-v2-git-providers";
import { OAuthV2GitProviderCard } from "#/components/features/settings/git-settings/oauth-v2-git-provider-card";
import type { Provider } from "#/types/settings";
import { ProjectManagementIntegration } from "#/components/features/settings/project-management/project-management-integration";
import { Text } from "#/ui/typography";
import { cn } from "#/utils/utils";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
} from "#/utils/settings-list-classes";
import { canAccessSuperAdminDashboard } from "#/utils/org/super-admin-access";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";

export const clientLoader = createPermissionGuard("manage_integrations");

const OAUTH_V2_GIT_PROVIDER_TYPES: Provider[] = [
  "github",
  "gitlab",
  "bitbucket",
  "bitbucket_data_center",
  "azure_devops",
  "forgejo",
];

function GitSettingsScreen() {
  const { t } = useTranslation();

  const { mutate: saveGitProviders, isPending } = useAddGitProviders();
  const { mutate: disconnectGitTokens, isPending: isDisconnecting } =
    useDeleteGitProviders();

  const { data: settings, isLoading } = useSettings();
  const { providers } = useUserProviders();

  const { data: config } = useConfig();
  const { data: me } = useMe();

  React.useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const jiraDcWebhookStatus = params.get("jira_dc_webhook");
    if (!jiraDcWebhookStatus) {
      return;
    }

    if (jiraDcWebhookStatus === "install_failed") {
      displayErrorToast(
        t(I18nKey.PROJECT_MANAGEMENT$JIRA_DC_WEBHOOK_INSTALL_FAILED),
      );
    } else if (jiraDcWebhookStatus === "installed") {
      displaySuccessToast(
        t(I18nKey.PROJECT_MANAGEMENT$JIRA_DC_WEBHOOK_SETUP_SAVED),
      );
    }

    params.delete("jira_dc_webhook");
    const query = params.toString();
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${query ? `?${query}` : ""}${
        window.location.hash
      }`,
    );
  }, [t]);

  React.useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const linkStatus = params.get("link_status");
    if (!linkStatus) {
      return;
    }

    displayErrorToast(t(I18nKey.GIT_PROVIDER$LINK_FAILED));

    params.delete("link_status");
    const query = params.toString();
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${query ? `?${query}` : ""}${
        window.location.hash
      }`,
    );
  }, [t]);

  const [githubTokenInputHasValue, setGithubTokenInputHasValue] =
    React.useState(false);
  const [gitlabTokenInputHasValue, setGitlabTokenInputHasValue] =
    React.useState(false);
  const [bitbucketTokenInputHasValue, setBitbucketTokenInputHasValue] =
    React.useState(false);
  const [bitbucketDCTokenInputHasValue, setBitbucketDCTokenInputHasValue] =
    React.useState(false);
  const [azureDevOpsTokenInputHasValue, setAzureDevOpsTokenInputHasValue] =
    React.useState(false);
  const [forgejoTokenInputHasValue, setForgejoTokenInputHasValue] =
    React.useState(false);

  const [githubHostInputHasValue, setGithubHostInputHasValue] =
    React.useState(false);
  const [gitlabHostInputHasValue, setGitlabHostInputHasValue] =
    React.useState(false);
  const [bitbucketHostInputHasValue, setBitbucketHostInputHasValue] =
    React.useState(false);
  const [bitbucketDCHostInputHasValue, setBitbucketDCHostInputHasValue] =
    React.useState(false);
  const [azureDevOpsHostInputHasValue, setAzureDevOpsHostInputHasValue] =
    React.useState(false);
  const [forgejoHostInputHasValue, setForgejoHostInputHasValue] =
    React.useState(false);

  const existingGithubHost = settings?.provider_tokens_set.github;
  const existingGitlabHost = settings?.provider_tokens_set.gitlab;
  const existingBitbucketHost = settings?.provider_tokens_set.bitbucket;
  const existingBitbucketDCHost =
    settings?.provider_tokens_set.bitbucket_data_center;
  const existingAzureDevOpsHost = settings?.provider_tokens_set.azure_devops;
  const existingForgejoHost = settings?.provider_tokens_set.forgejo;

  const isSaas = config?.app_mode === "saas";
  const isOAuthV2 =
    isSaas && Boolean(config?.feature_flags?.enable_oauth_v2_login);
  const { data: oauthV2GitProviders, isSuccess: hasLoadedOAuthV2GitProviders } =
    useOAuthV2GitProviders(isOAuthV2);
  const oauthV2Cards = (oauthV2GitProviders ?? []).filter((row) =>
    OAUTH_V2_GIT_PROVIDER_TYPES.includes(row.provider_type as Provider),
  );
  const isGitHubTokenSet = providers.includes("github");
  const isGitLabTokenSet = providers.includes("gitlab");
  const isBitbucketTokenSet = providers.includes("bitbucket");
  const isBitbucketDCTokenSet = providers.includes("bitbucket_data_center");
  const isAzureDevOpsTokenSet = providers.includes("azure_devops");
  const isForgejoTokenSet = providers.includes("forgejo");

  const formAction = async (formData: FormData) => {
    const disconnectButtonClicked =
      formData.get("disconnect-tokens-button") !== null;

    if (disconnectButtonClicked) {
      disconnectGitTokens(undefined, {
        onSuccess: () => {
          displaySuccessToast(t(I18nKey.SETTINGS$SAVED));
        },
        onError: (error) => {
          const errorMessage = retrieveAxiosErrorMessage(error);
          displayErrorToast(errorMessage || t(I18nKey.ERROR$GENERIC));
        },
      });
      return;
    }

    const githubToken = (
      formData.get("github-token-input")?.toString() || ""
    ).trim();
    const gitlabToken = (
      formData.get("gitlab-token-input")?.toString() || ""
    ).trim();
    const bitbucketToken = (
      formData.get("bitbucket-token-input")?.toString() || ""
    ).trim();
    const bitbucketDCToken = (
      formData.get("bitbucket-dc-token-input")?.toString() || ""
    ).trim();
    const azureDevOpsToken = (
      formData.get("azure-devops-token-input")?.toString() || ""
    ).trim();
    const forgejoToken = (
      formData.get("forgejo-token-input")?.toString() || ""
    ).trim();
    const githubHost = (
      formData.get("github-host-input")?.toString() || ""
    ).trim();
    const gitlabHost = (
      formData.get("gitlab-host-input")?.toString() || ""
    ).trim();
    const bitbucketHost = (
      formData.get("bitbucket-host-input")?.toString() || ""
    ).trim();
    const bitbucketDCHost = (
      formData.get("bitbucket-dc-host-input")?.toString() || ""
    ).trim();
    const azureDevOpsHost = (
      formData.get("azure-devops-host-input")?.toString() || ""
    ).trim();
    const forgejoHost = (
      formData.get("forgejo-host-input")?.toString() || ""
    ).trim();

    const providerTokens: Record<string, { token: string; host: string }> = {
      github: { token: githubToken, host: githubHost },
      gitlab: { token: gitlabToken, host: gitlabHost },
      bitbucket: { token: bitbucketToken, host: bitbucketHost },
      bitbucket_data_center: { token: bitbucketDCToken, host: bitbucketDCHost },
      azure_devops: { token: azureDevOpsToken, host: azureDevOpsHost },
      forgejo: { token: forgejoToken, host: forgejoHost },
    };

    saveGitProviders(
      {
        providers: providerTokens,
      },
      {
        onSuccess: () => {
          displaySuccessToast(t(I18nKey.SETTINGS$SAVED));
        },
        onError: (error) => {
          const errorMessage = retrieveAxiosErrorMessage(error);
          displayErrorToast(errorMessage || t(I18nKey.ERROR$GENERIC));
        },
        onSettled: () => {
          setGithubTokenInputHasValue(false);
          setGitlabTokenInputHasValue(false);
          setBitbucketTokenInputHasValue(false);
          setBitbucketDCTokenInputHasValue(false);
          setAzureDevOpsTokenInputHasValue(false);
          setForgejoTokenInputHasValue(false);
          setGithubHostInputHasValue(false);
          setGitlabHostInputHasValue(false);
          setBitbucketHostInputHasValue(false);
          setBitbucketDCHostInputHasValue(false);
          setAzureDevOpsHostInputHasValue(false);
          setForgejoHostInputHasValue(false);
        },
      },
    );
  };

  const formIsClean =
    !githubTokenInputHasValue &&
    !gitlabTokenInputHasValue &&
    !bitbucketTokenInputHasValue &&
    !bitbucketDCTokenInputHasValue &&
    !azureDevOpsTokenInputHasValue &&
    !forgejoTokenInputHasValue &&
    !githubHostInputHasValue &&
    !gitlabHostInputHasValue &&
    !bitbucketHostInputHasValue &&
    !bitbucketDCHostInputHasValue &&
    !azureDevOpsHostInputHasValue &&
    !forgejoHostInputHasValue;
  // With OAuth v2 the cards come from `auth_providers`, not env-driven config.
  const isEnvProviderCardVisible = isSaas && !isOAuthV2;
  const shouldRenderGitHubSection =
    isEnvProviderCardVisible &&
    Boolean(config?.providers_configured?.includes("github"));
  const shouldRenderGitHubConfigureButton =
    shouldRenderGitHubSection && isGitHubTokenSet && config?.github_app_slug;
  const shouldRenderGitLabSection =
    isEnvProviderCardVisible && Boolean(config?.gitlab_enabled);
  const shouldRenderBitbucketSection =
    isEnvProviderCardVisible &&
    Boolean(config?.providers_configured?.includes("bitbucket"));
  const shouldRenderBitbucketDCSection =
    isEnvProviderCardVisible &&
    Boolean(config?.providers_configured?.includes("bitbucket_data_center"));
  const shouldRenderAzureDevOpsSection =
    isEnvProviderCardVisible &&
    Boolean(config?.providers_configured?.includes("azure_devops"));
  const shouldRenderSlackSection = isSaas && Boolean(config?.slack_enabled);
  const { data: slackStatus } = useSlackStatus(shouldRenderSlackSection);
  // A server without the status route answers with the app's HTML page.
  const isSlackConnected =
    typeof slackStatus?.connected === "boolean"
      ? slackStatus.connected
      : undefined;
  const shouldRenderProjectManagementIntegrations =
    config?.feature_flags?.enable_jira ||
    config?.feature_flags?.enable_jira_dc ||
    config?.feature_flags?.enable_linear;
  const hasSaasProviderCards =
    oauthV2Cards.length > 0 ||
    shouldRenderGitHubSection ||
    shouldRenderGitLabSection ||
    shouldRenderBitbucketSection ||
    shouldRenderBitbucketDCSection ||
    shouldRenderAzureDevOpsSection ||
    shouldRenderSlackSection;
  // Without this the page shows only its heading when nothing is configured.
  // Wait for the v2 list so the message doesn't flash before the cards load.
  const shouldRenderEmptyState =
    isSaas &&
    (!isOAuthV2 || hasLoadedOAuthV2GitProviders) &&
    !hasSaasProviderCards &&
    !shouldRenderProjectManagementIntegrations;
  // With OAuth v2, a super admin adds git providers under Auth Providers.
  const canAddGitProvider =
    isOAuthV2 &&
    canAccessSuperAdminDashboard(config?.feature_flags, me?.permissions);

  const connectedStatusLabel = (
    isConnected: boolean,
    disconnectedKey: I18nKey,
  ) => (isConnected ? t(I18nKey.STATUS$CONNECTED) : t(disconnectedKey));

  return (
    <form
      data-testid="git-settings-screen"
      action={formAction}
      className="flex h-full flex-col justify-between gap-6"
    >
      {!isLoading && (
        <div className="flex flex-col gap-6">
          {hasSaasProviderCards && (
            <div className="flex flex-col gap-3">
              <Text className="text-sm font-medium text-content-2">
                {t(I18nKey.SETTINGS$GIT_PROVIDERS)}
              </Text>
              <div
                className={cn(
                  settingsListContainerClassName,
                  settingsListDividerClassName,
                )}
              >
                {oauthV2Cards.map((row) => (
                  <OAuthV2GitProviderCard key={row.id} row={row} />
                ))}

                {shouldRenderGitHubSection && (
                  <IntegrationProviderCard
                    provider="github"
                    title={t(I18nKey.SETTINGS$GITHUB)}
                    isConnected={isGitHubTokenSet}
                    statusTestId="github-status-text"
                    statusLabel={connectedStatusLabel(
                      isGitHubTokenSet,
                      I18nKey.STATUS$NOT_CONNECTED,
                    )}
                    action={
                      <GitProviderConnection
                        provider="github"
                        providerName={t(I18nKey.SETTINGS$GITHUB)}
                        isConnected={isGitHubTokenSet}
                      >
                        {shouldRenderGitHubConfigureButton && (
                          <ConfigureGitHubRepositoriesAnchor
                            slug={config.github_app_slug!}
                            isInstalled
                          />
                        )}
                      </GitProviderConnection>
                    }
                  />
                )}

                {shouldRenderGitLabSection && (
                  <IntegrationProviderCard
                    provider="gitlab"
                    title={t(I18nKey.SETTINGS$GITLAB)}
                    isConnected={isGitLabTokenSet}
                    statusTestId="gitlab-status-text"
                    statusLabel={connectedStatusLabel(
                      isGitLabTokenSet,
                      I18nKey.SETTINGS$GITLAB_NOT_CONNECTED,
                    )}
                    action={
                      <GitProviderConnection
                        provider="gitlab"
                        providerName={t(I18nKey.SETTINGS$GITLAB)}
                        isConnected={isGitLabTokenSet}
                      />
                    }
                  >
                    {isGitLabTokenSet ? <GitLabWebhookManager /> : null}
                  </IntegrationProviderCard>
                )}

                {shouldRenderBitbucketSection && (
                  <IntegrationProviderCard
                    provider="bitbucket"
                    title={t(I18nKey.SETTINGS$BITBUCKET)}
                    isConnected={isBitbucketTokenSet}
                    statusTestId="bitbucket-status-text"
                    statusLabel={connectedStatusLabel(
                      isBitbucketTokenSet,
                      I18nKey.STATUS$NOT_CONNECTED,
                    )}
                    action={
                      <GitProviderConnection
                        provider="bitbucket"
                        providerName={t(I18nKey.SETTINGS$BITBUCKET)}
                        isConnected={isBitbucketTokenSet}
                      />
                    }
                  />
                )}

                {shouldRenderBitbucketDCSection && (
                  <IntegrationProviderCard
                    provider="bitbucket_data_center"
                    title={t(
                      I18nKey.BITBUCKET_DATA_CENTER$WEBHOOK_SECTION_TITLE,
                    )}
                    isConnected={isBitbucketDCTokenSet}
                    statusTestId="bitbucket-dc-status-text"
                    statusLabel={connectedStatusLabel(
                      isBitbucketDCTokenSet,
                      I18nKey.BITBUCKET_DATA_CENTER$NOT_CONNECTED,
                    )}
                  >
                    {isBitbucketDCTokenSet ? (
                      <BitbucketDCWebhookManager />
                    ) : null}
                  </IntegrationProviderCard>
                )}

                {shouldRenderAzureDevOpsSection && (
                  <IntegrationProviderCard
                    provider="azure_devops"
                    title={t(I18nKey.SETTINGS$AZURE_DEVOPS)}
                    isConnected={isAzureDevOpsTokenSet}
                    statusTestId="azure-devops-status-text"
                    statusLabel={connectedStatusLabel(
                      isAzureDevOpsTokenSet,
                      I18nKey.AZURE_DEVOPS$NOT_CONNECTED,
                    )}
                    action={
                      !isAzureDevOpsTokenSet ? (
                        <ConfigureAzureDevOpsAnchor />
                      ) : undefined
                    }
                  >
                    {isAzureDevOpsTokenSet ? (
                      <AzureDevOpsWebhookManager />
                    ) : null}
                  </IntegrationProviderCard>
                )}

                {shouldRenderSlackSection && (
                  <IntegrationProviderCard
                    provider="slack"
                    title={t(I18nKey.SETTINGS$SLACK)}
                    isConnected={isSlackConnected}
                    statusTestId="slack-status-text"
                    statusLabel={
                      isSlackConnected !== undefined
                        ? connectedStatusLabel(
                            isSlackConnected,
                            I18nKey.STATUS$NOT_CONNECTED,
                          )
                        : undefined
                    }
                    action={<InstallSlackAppAnchor />}
                  />
                )}
              </div>
            </div>
          )}

          {shouldRenderEmptyState && (
            <div
              data-testid="integrations-empty-state"
              className={cn(settingsListContainerClassName, "p-8 text-center")}
            >
              <p className="text-sm text-[var(--oh-muted)]">
                {t(I18nKey.SETTINGS$INTEGRATIONS_EMPTY)}{" "}
                {canAddGitProvider ? (
                  <Trans
                    i18nKey={
                      I18nKey.SETTINGS$INTEGRATIONS_EMPTY_ADD_GIT_PROVIDER
                    }
                    components={{
                      a: (
                        <Link
                          to={SUPER_ADMIN_PATHS.authProviders}
                          className="underline underline-offset-2"
                        >
                          {t(I18nKey.SUPER_ADMIN$NAV_AUTH_PROVIDERS)}
                        </Link>
                      ),
                    }}
                  />
                ) : (
                  t(I18nKey.SETTINGS$INTEGRATIONS_EMPTY_ASK_ADMIN)
                )}
              </p>
            </div>
          )}

          {shouldRenderProjectManagementIntegrations && (
            <ProjectManagementIntegration />
          )}

          {!isSaas && (
            <div className="flex flex-col gap-6">
              <GitHubTokenInput
                name="github-token-input"
                isGitHubTokenSet={isGitHubTokenSet}
                onChange={(value) => {
                  setGithubTokenInputHasValue(!!value);
                }}
                onGitHubHostChange={(value) => {
                  setGithubHostInputHasValue(!!value);
                }}
                githubHostSet={existingGithubHost}
              />

              <GitLabTokenInput
                name="gitlab-token-input"
                isGitLabTokenSet={isGitLabTokenSet}
                onChange={(value) => {
                  setGitlabTokenInputHasValue(!!value);
                }}
                onGitLabHostChange={(value) => {
                  setGitlabHostInputHasValue(!!value);
                }}
                gitlabHostSet={existingGitlabHost}
              />

              <BitbucketTokenInput
                name="bitbucket-token-input"
                isBitbucketTokenSet={isBitbucketTokenSet}
                onChange={(value) => {
                  setBitbucketTokenInputHasValue(!!value);
                }}
                onBitbucketHostChange={(value) => {
                  setBitbucketHostInputHasValue(!!value);
                }}
                bitbucketHostSet={existingBitbucketHost}
              />

              <BitbucketDCTokenInput
                name="bitbucket-dc-token-input"
                isBitbucketDCTokenSet={isBitbucketDCTokenSet}
                onChange={(value) => {
                  setBitbucketDCTokenInputHasValue(!!value);
                }}
                onBitbucketDCHostChange={(value) => {
                  setBitbucketDCHostInputHasValue(!!value);
                }}
                bitbucketDCHostSet={existingBitbucketDCHost}
              />

              <AzureDevOpsTokenInput
                name="azure-devops-token-input"
                isAzureDevOpsTokenSet={isAzureDevOpsTokenSet}
                onChange={(value) => {
                  setAzureDevOpsTokenInputHasValue(!!value);
                }}
                onAzureDevOpsHostChange={(value) => {
                  setAzureDevOpsHostInputHasValue(!!value);
                }}
                azureDevOpsHostSet={existingAzureDevOpsHost}
              />

              <ForgejoTokenInput
                name="forgejo-token-input"
                isForgejoTokenSet={isForgejoTokenSet}
                onChange={(value) => {
                  setForgejoTokenInputHasValue(!!value);
                }}
                onForgejoHostChange={(value) => {
                  setForgejoHostInputHasValue(!!value);
                }}
                forgejoHostSet={existingForgejoHost}
              />
            </div>
          )}
        </div>
      )}

      {isLoading && (
        <GitSettingInputsSkeleton
          variant={config?.app_mode === "saas" ? "saas" : "oss"}
        />
      )}

      {!isSaas && (
        <div className="flex justify-end gap-3">
          <BrandButton
            testId="disconnect-tokens-button"
            name="disconnect-tokens-button"
            type="submit"
            variant="secondary"
            isDisabled={
              isDisconnecting ||
              (!isGitHubTokenSet &&
                !isGitLabTokenSet &&
                !isBitbucketTokenSet &&
                !isBitbucketDCTokenSet &&
                !isAzureDevOpsTokenSet &&
                !isForgejoTokenSet)
            }
          >
            {t(I18nKey.GIT$DISCONNECT_TOKENS)}
          </BrandButton>
          <BrandButton
            testId="submit-button"
            type="submit"
            variant="primary"
            isDisabled={isPending || formIsClean}
          >
            {!isPending && t("SETTINGS$SAVE_CHANGES")}
            {isPending && t("SETTINGS$SAVING")}
          </BrandButton>
        </div>
      )}
    </form>
  );
}

export default GitSettingsScreen;
