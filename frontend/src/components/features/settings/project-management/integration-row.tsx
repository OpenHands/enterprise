import React from "react";
import { useTranslation } from "react-i18next";

import { useIntegrationStatus } from "#/hooks/query/use-integration-status";
import { useLinkIntegration } from "#/hooks/mutation/use-link-integration";
import { useUnlinkIntegration } from "#/hooks/mutation/use-unlink-integration";
import { useConfigureIntegration } from "#/hooks/mutation/use-configure-integration";
import { useConfig } from "#/hooks/query/use-config";
import { useMe } from "#/hooks/query/use-me";
import { usePermission } from "#/hooks/organizations/use-permissions";
import { useJiraInstanceStatus } from "#/hooks/query/use-jira-instance-status";
import { I18nKey } from "#/i18n/declaration";
import {
  ConfigureButton,
  ConfigureModal,
} from "#/components/features/settings/project-management/configure-modal";
import { Text } from "#/ui/typography";
import { cn } from "#/utils/utils";
import { settingsListRowHoverClassName } from "#/utils/settings-list-classes";
import { formControlTransitionClassName } from "#/utils/form-control-classes";
import { IntegrationProviderIcon } from "#/components/features/settings/git-settings/integration-provider-icon";

interface IntegrationRowProps {
  platform: "jira" | "jira-dc" | "linear";
  platformName: string;
  "data-testid"?: string;
}

export function IntegrationRow({
  platform,
  platformName,
  "data-testid": dataTestId,
}: IntegrationRowProps) {
  const [isConfigureModalOpen, setConfigureModalOpen] = React.useState(false);
  const { t } = useTranslation();

  const { data: integrationData, isLoading: isStatusLoading } =
    useIntegrationStatus(platform);

  // Jira Cloud only: setting up the workspace connection is admin/owner-only;
  // members link their own account (OAuth mode) or are matched by email.
  const { data: config } = useConfig();
  const { data: me } = useMe();
  const { hasPermission } = usePermission(me?.role ?? "member");
  const isJira = platform === "jira";
  const canConfigure = !isJira || hasPermission("manage_integration_providers");
  const jiraOauthEnabled = config?.jira_oauth_enabled ?? true;

  // A member in email mode has nothing to configure or link (matching happens
  // by email at webhook time), so the row shows guidance instead of a button.
  const memberEmailMode = isJira && !canConfigure && !jiraOauthEnabled;
  const { data: jiraInstanceStatus } = useJiraInstanceStatus(memberEmailMode);

  const linkMutation = useLinkIntegration(platform, {
    onSettled: () => {
      setConfigureModalOpen(false);
    },
  });

  const unlinkMutation = useUnlinkIntegration(platform, {
    onSettled: () => {
      setConfigureModalOpen(false);
    },
  });

  const configureMutation = useConfigureIntegration(platform, {
    onSettled: () => {
      setConfigureModalOpen(false);
    },
  });

  const handleConfigure = () => {
    setConfigureModalOpen(true);
  };

  const handleLink = (workspace: string) => {
    linkMutation.mutate(workspace);
  };

  const handleUnlink = (adminApiKey?: string) => {
    unlinkMutation.mutate(adminApiKey);
  };

  const handleConfigureConfirm = (data: {
    workspace: string;
    webhookSecret: string;
    serviceAccountEmail: string;
    serviceAccountApiKey: string;
    adminApiKey: string;
    isActive: boolean;
  }) => {
    configureMutation.mutate(data);
  };

  const isLoading =
    isStatusLoading ||
    linkMutation.isPending ||
    unlinkMutation.isPending ||
    configureMutation.isPending;

  const isIntegrationActive = integrationData?.status === "active";
  const hasWorkspace = integrationData?.workspace;

  const buttonText =
    isIntegrationActive && hasWorkspace
      ? t(I18nKey.PROJECT_MANAGEMENT$EDIT_BUTTON_LABEL)
      : t(I18nKey.PROJECT_MANAGEMENT$CONFIGURE_BUTTON_LABEL);

  if (memberEmailMode) {
    return (
      <div
        className={cn(
          "flex items-center justify-between gap-4 px-3 py-3",
          formControlTransitionClassName,
          settingsListRowHoverClassName,
        )}
        data-testid={dataTestId}
      >
        <div className="flex min-w-0 items-center gap-3">
          <IntegrationProviderIcon provider={platform} />
          <Text className="min-w-0 truncate text-sm font-medium text-content-2">
            {platformName}
          </Text>
        </div>
        {jiraInstanceStatus !== undefined && (
          <span
            className="text-sm text-muted"
            data-testid="jira-member-guidance"
          >
            {t(
              jiraInstanceStatus.configured
                ? I18nKey.PROJECT_MANAGEMENT$JIRA_MEMBER_CONFIGURED_PROMPT
                : I18nKey.PROJECT_MANAGEMENT$JIRA_MEMBER_NOT_CONFIGURED_PROMPT,
            )}
          </span>
        )}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex items-center justify-between gap-4 px-3 py-3",
        formControlTransitionClassName,
        settingsListRowHoverClassName,
      )}
      data-testid={dataTestId}
    >
      <div className="flex min-w-0 items-center gap-3">
        <IntegrationProviderIcon provider={platform} />
        <Text className="min-w-0 truncate text-sm font-medium text-content-2">
          {platformName}
        </Text>
      </div>
      <ConfigureButton
        onClick={handleConfigure}
        isDisabled={isLoading}
        text={buttonText}
        data-testid={`${platform}-configure-button`}
      />
      <ConfigureModal
        isOpen={isConfigureModalOpen}
        onClose={() => setConfigureModalOpen(false)}
        onConfirm={handleConfigureConfirm}
        onLink={handleLink}
        onUnlink={handleUnlink}
        platformName={platformName}
        platform={platform}
        integrationData={integrationData}
        canConfigure={canConfigure}
      />
    </div>
  );
}
