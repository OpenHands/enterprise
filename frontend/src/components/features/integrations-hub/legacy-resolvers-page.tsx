import {
  useEffect,
  useMemo,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Settings } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router";
import {
  HubModal,
  hubModalBodyClassName,
} from "#/components/features/integrations-hub/hub-modal";
import { HubProviderModalHeader } from "#/components/features/integrations-hub/hub-provider-modal-header";
import { IntegrationsHubPageHeader } from "#/components/features/integrations-hub/integrations-hub-page-header";
import {
  isLegacyResolverId,
  LEGACY_RESOLVERS,
  type LegacyResolverDefinition,
} from "#/components/features/integrations-hub/legacy-resolvers";
import { BrandButton } from "#/components/features/settings/brand-button";
import { AzureDevOpsWebhookManager } from "#/components/features/settings/git-settings/azure-devops-webhook-manager";
import { BitbucketDCWebhookManager } from "#/components/features/settings/git-settings/bitbucket-dc-webhook-manager";
import { GitLabWebhookManager } from "#/components/features/settings/git-settings/gitlab-webhook-manager";
import { IntegrationProviderIcon } from "#/components/features/settings/git-settings/integration-provider-icon";
import type { IntegrationProviderId } from "#/components/features/settings/git-settings/integration-provider-icon";
import { ConfigureModal } from "#/components/features/settings/project-management/configure-modal";
import { JiraDcIntegrationPanel } from "#/components/features/settings/project-management/jira-dc-integration-panel";
import { useConfigureIntegration } from "#/hooks/mutation/use-configure-integration";
import { useLinkIntegration } from "#/hooks/mutation/use-link-integration";
import { useUnlinkIntegration } from "#/hooks/mutation/use-unlink-integration";
import { useConfig } from "#/hooks/query/use-config";
import { useIntegrationStatus } from "#/hooks/query/use-integration-status";
import { useAuthUrl } from "#/hooks/use-auth-url";
import { useUserProviders } from "#/hooks/use-user-providers";
import { I18nKey } from "#/i18n/declaration";
import { GitSettingsScreen } from "#/routes/git-settings";
import { Text } from "#/ui/typography";
import { formControlTransitionClassName } from "#/utils/form-control-classes";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
  settingsListRowActionButtonClassName,
  settingsListRowHoverClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";

type WebhookManageProvider =
  | "gitlab"
  | "azure_devops"
  | "bitbucket_data_center";
type ProjectManagementPlatform = "jira" | "linear";
type TokenSetupProvider = "bitbucket" | "bitbucket_data_center" | "forgejo";

function ConnectedBadge({
  connected,
  testId,
}: {
  connected: boolean;
  testId: string;
}) {
  const { t } = useTranslation();
  if (!connected) {
    return null;
  }
  return (
    <span
      data-testid={testId}
      className="inline-flex shrink-0 items-center rounded-md bg-green-500/15 px-2 py-0.5 text-xs font-medium whitespace-nowrap text-green-400"
    >
      {t(I18nKey.STATUS$CONNECTED)}
    </span>
  );
}

function ActionButton({
  testId,
  label,
  connected,
  onClick,
}: {
  testId: string;
  label: string;
  connected: boolean;
  onClick: () => void;
}) {
  return (
    <BrandButton
      testId={testId}
      type="button"
      variant={connected ? "secondary" : "primary"}
      onClick={(event) => {
        event?.stopPropagation();
        onClick();
      }}
      className={settingsListRowActionButtonClassName}
      startContent={
        connected ? (
          <Settings className="size-3.5 shrink-0" aria-hidden />
        ) : undefined
      }
    >
      {label}
    </BrandButton>
  );
}

function WebhookManageModal({
  resolver,
  onClose,
}: {
  resolver: LegacyResolverDefinition;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  let body: ReactNode = null;
  if (resolver.id === "gitlab") {
    body = <GitLabWebhookManager />;
  } else if (resolver.id === "azure_devops") {
    body = <AzureDevOpsWebhookManager />;
  } else if (resolver.id === "bitbucket_data_center") {
    body = <BitbucketDCWebhookManager />;
  }

  return (
    <HubModal
      ariaLabel={t(resolver.nameKey)}
      testId={`integrations-hub-resolver-modal-${resolver.id}`}
      width="xl"
      onClose={onClose}
    >
      <div className={hubModalBodyClassName}>
        <HubProviderModalHeader
          provider={resolver.id}
          title={t(resolver.nameKey)}
          subtitle={t(resolver.sublineKey)}
          className="mb-6"
        />
        {body}
      </div>
    </HubModal>
  );
}

function TokenSetupModal({
  resolver,
  onClose,
}: {
  resolver: LegacyResolverDefinition;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <HubModal
      ariaLabel={t(resolver.nameKey)}
      testId={`integrations-hub-resolver-modal-${resolver.id}`}
      width="xl"
      onClose={onClose}
    >
      <div className={hubModalBodyClassName}>
        <HubProviderModalHeader
          provider={resolver.id}
          title={t(resolver.nameKey)}
          subtitle={t(resolver.sublineKey)}
          className="mb-6"
        />
        <GitSettingsScreen focusProvider={resolver.id as TokenSetupProvider} />
      </div>
    </HubModal>
  );
}

function ProjectManagementConfigureModal({
  platform,
  isOpen,
  onClose,
}: {
  platform: ProjectManagementPlatform;
  isOpen: boolean;
  onClose: () => void;
}) {
  const { data: integrationData } = useIntegrationStatus(platform);
  const linkMutation = useLinkIntegration(platform, {
    onSettled: onClose,
  });
  const unlinkMutation = useUnlinkIntegration(platform, {
    onSettled: onClose,
  });
  const configureMutation = useConfigureIntegration(platform, {
    onSettled: onClose,
  });

  return (
    <ConfigureModal
      isOpen={isOpen}
      onClose={onClose}
      onConfirm={(data) => configureMutation.mutate(data)}
      onLink={(workspace) => linkMutation.mutate(workspace)}
      onUnlink={(adminApiKey) => unlinkMutation.mutate(adminApiKey)}
      platformName={platform === "jira" ? "Jira Cloud" : "Linear"}
      platform={platform}
      integrationData={integrationData}
    />
  );
}

function openExternal(url: string, sameWindow = false) {
  if (sameWindow) {
    window.location.href = url;
    return;
  }
  window.open(url, "_blank", "noreferrer noopener");
}

export function LegacyResolversPage() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { data: config } = useConfig();
  const { providers } = useUserProviders();
  const { data: jiraStatus } = useIntegrationStatus("jira");
  const { data: jiraDcStatus } = useIntegrationStatus("jira-dc");
  const { data: linearStatus } = useIntegrationStatus("linear");
  const gitlabAuthUrl = useAuthUrl({
    appMode: config?.app_mode ?? null,
    identityProvider: "gitlab",
    authUrl: config?.auth_url,
  });
  const azureAuthUrl = useAuthUrl({
    appMode: config?.app_mode ?? null,
    identityProvider: "azure_devops",
    authUrl: config?.auth_url,
  });
  const [webhookModalId, setWebhookModalId] =
    useState<WebhookManageProvider | null>(null);
  const [jiraDcModalOpen, setJiraDcModalOpen] = useState(false);
  const [tokenSetupId, setTokenSetupId] = useState<TokenSetupProvider | null>(
    null,
  );
  const [projectManagementPlatform, setProjectManagementPlatform] =
    useState<ProjectManagementPlatform | null>(null);

  useEffect(() => {
    const openResolver = (location.state as { openResolver?: string } | null)
      ?.openResolver;
    if (!isLegacyResolverId(openResolver)) {
      return;
    }
    if (
      openResolver === "gitlab" ||
      openResolver === "azure_devops" ||
      openResolver === "bitbucket_data_center"
    ) {
      setWebhookModalId(openResolver);
    } else if (openResolver === "jira-dc") {
      setJiraDcModalOpen(true);
    } else if (openResolver === "jira" || openResolver === "linear") {
      setProjectManagementPlatform(openResolver);
    } else if (openResolver === "bitbucket" || openResolver === "forgejo") {
      setTokenSetupId(openResolver);
    }
    navigate(location.pathname, { replace: true, state: null });
  }, [location.pathname, location.state, navigate]);

  const connectedById = useMemo(() => {
    const map = new Map<IntegrationProviderId, boolean>();
    for (const id of [
      "github",
      "gitlab",
      "bitbucket",
      "bitbucket_data_center",
      "azure_devops",
      "forgejo",
    ] as const) {
      map.set(id, providers.includes(id));
    }
    map.set(
      "jira",
      jiraStatus?.status === "active" && Boolean(jiraStatus?.workspace),
    );
    map.set(
      "jira-dc",
      jiraDcStatus?.status === "active" && Boolean(jiraDcStatus?.workspace),
    );
    map.set(
      "linear",
      linearStatus?.status === "active" && Boolean(linearStatus?.workspace),
    );
    map.set("slack", false);
    return map;
  }, [providers, jiraStatus, jiraDcStatus, linearStatus]);

  const activateResolver = (resolverId: IntegrationProviderId) => {
    const connected = connectedById.get(resolverId) === true;
    switch (resolverId) {
      case "github": {
        const slug = config?.github_app_slug;
        if (!slug) {
          return;
        }
        openExternal(`https://github.com/apps/${slug}/installations/new`);
        return;
      }
      case "gitlab":
        if (connected) {
          setWebhookModalId("gitlab");
          return;
        }
        if (gitlabAuthUrl) {
          openExternal(gitlabAuthUrl, true);
        }
        return;
      case "azure_devops":
        if (connected) {
          setWebhookModalId("azure_devops");
          return;
        }
        if (azureAuthUrl) {
          openExternal(azureAuthUrl, true);
        }
        return;
      case "bitbucket_data_center":
        if (connected) {
          setWebhookModalId("bitbucket_data_center");
          return;
        }
        setTokenSetupId("bitbucket_data_center");
        return;
      case "bitbucket":
        setTokenSetupId("bitbucket");
        return;
      case "forgejo":
        setTokenSetupId("forgejo");
        return;
      case "slack":
        openExternal("/slack/install");
        return;
      case "jira":
      case "linear":
        setProjectManagementPlatform(resolverId);
        return;
      case "jira-dc":
        setJiraDcModalOpen(true);
        break;
      default:
        break;
    }
  };

  const actionLabelFor = (
    resolverId: IntegrationProviderId,
    connected: boolean,
  ): string | null => {
    switch (resolverId) {
      case "github":
        if (!config?.github_app_slug) {
          return null;
        }
        return connected
          ? t(I18nKey.PROJECT_MANAGEMENT$CONFIGURE_BUTTON_LABEL)
          : t(I18nKey.SETTINGS$CONNECT);
      case "gitlab":
      case "azure_devops":
      case "bitbucket":
      case "forgejo":
      case "bitbucket_data_center":
        return connected
          ? t(I18nKey.PROJECT_MANAGEMENT$CONFIGURE_BUTTON_LABEL)
          : t(I18nKey.SETTINGS$CONNECT);
      case "slack":
        return t(I18nKey.SETTINGS$INSTALL);
      case "jira":
      case "linear":
        return connected
          ? t(I18nKey.PROJECT_MANAGEMENT$EDIT_BUTTON_LABEL)
          : t(I18nKey.SETTINGS$CONNECT);
      case "jira-dc":
        return connected
          ? t(I18nKey.PROJECT_MANAGEMENT$CONFIGURE_BUTTON_LABEL)
          : t(I18nKey.SETTINGS$CONNECT);
      default:
        return null;
    }
  };

  const webhookResolver = LEGACY_RESOLVERS.find(
    (resolver) => resolver.id === webhookModalId,
  );
  const tokenSetupResolver = LEGACY_RESOLVERS.find(
    (resolver) => resolver.id === tokenSetupId,
  );

  return (
    <div
      className="flex flex-col gap-6"
      data-testid="integrations-hub-resolvers"
    >
      <IntegrationsHubPageHeader
        title={I18nKey.INTEGRATIONS_HUB$NAV_RESOLVERS_LEGACY}
        subtitle={I18nKey.INTEGRATIONS_HUB$PAGE_RESOLVERS_SUBLINE}
      />

      <div
        className={cn(
          settingsListContainerClassName,
          settingsListDividerClassName,
        )}
        data-testid="integrations-hub-resolvers-list"
      >
        {LEGACY_RESOLVERS.map((resolver) => {
          const connected = connectedById.get(resolver.id) === true;
          const actionLabel = actionLabelFor(resolver.id, connected);
          const canActivate = actionLabel !== null;
          const isManageAction = connected && resolver.id !== "slack";

          const onActivate = () => {
            if (!canActivate) {
              return;
            }
            activateResolver(resolver.id);
          };

          const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
            if (!canActivate) {
              return;
            }
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              onActivate();
            }
          };

          return (
            <div
              key={resolver.id}
              role="button"
              tabIndex={canActivate ? 0 : -1}
              aria-disabled={!canActivate}
              data-testid={`integrations-hub-resolver-row-${resolver.id}`}
              onClick={canActivate ? onActivate : undefined}
              onKeyDown={canActivate ? onKeyDown : undefined}
              className={cn(
                "flex items-center justify-between gap-4 px-3 py-3",
                formControlTransitionClassName,
                settingsListRowHoverClassName,
                canActivate ? "cursor-pointer" : "cursor-default",
              )}
            >
              <div className="flex min-w-0 items-center gap-3">
                <IntegrationProviderIcon provider={resolver.id} size="md" />
                <div className="flex min-w-0 flex-col gap-0.5">
                  <div className="flex min-w-0 items-center gap-2">
                    <Text className="truncate text-sm font-medium leading-5 text-content-2">
                      {t(resolver.nameKey)}
                    </Text>
                    <ConnectedBadge
                      connected={connected}
                      testId={`integrations-hub-resolver-connected-${resolver.id}`}
                    />
                  </div>
                  <Text className="text-xs leading-4 text-[var(--oh-muted)]">
                    {t(resolver.sublineKey)}
                  </Text>
                </div>
              </div>
              {actionLabel ? (
                <ActionButton
                  testId={`integrations-hub-resolver-action-${resolver.id}`}
                  label={actionLabel}
                  connected={isManageAction}
                  onClick={onActivate}
                />
              ) : null}
            </div>
          );
        })}
      </div>

      {webhookResolver ? (
        <WebhookManageModal
          resolver={webhookResolver}
          onClose={() => setWebhookModalId(null)}
        />
      ) : null}
      {jiraDcModalOpen ? (
        <JiraDcIntegrationPanel
          directConfigure
          onConfigureDismiss={() => setJiraDcModalOpen(false)}
        />
      ) : null}
      {tokenSetupResolver ? (
        <TokenSetupModal
          resolver={tokenSetupResolver}
          onClose={() => setTokenSetupId(null)}
        />
      ) : null}
      {projectManagementPlatform ? (
        <ProjectManagementConfigureModal
          platform={projectManagementPlatform}
          isOpen
          onClose={() => setProjectManagementPlatform(null)}
        />
      ) : null}
    </div>
  );
}
