import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import type { IntegrationProviderId } from "#/components/features/settings/git-settings/integration-provider-icon";
import { IntegrationRow } from "./integration-row";
import { JiraDcIntegrationPanel } from "./jira-dc-integration-panel";
import { useConfig } from "#/hooks/query/use-config";
import { Text } from "#/ui/typography";
import { cn } from "#/utils/utils";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
} from "#/utils/settings-list-classes";

interface ProjectManagementIntegrationProps {
  /** When set, only render this project-management provider. */
  focusProvider?: IntegrationProviderId;
}

export function ProjectManagementIntegration({
  focusProvider,
}: ProjectManagementIntegrationProps = {}) {
  const { t } = useTranslation();
  const { data: config } = useConfig();

  const jiraEnabled =
    focusProvider === "jira" ||
    (!focusProvider && !!config?.feature_flags?.enable_jira);
  const linearEnabled =
    focusProvider === "linear" ||
    (!focusProvider && !!config?.feature_flags?.enable_linear);
  const jiraDcEnabled =
    focusProvider === "jira-dc" ||
    (!focusProvider && !!config?.feature_flags?.enable_jira_dc);

  if (!jiraEnabled && !linearEnabled && !jiraDcEnabled) {
    return null;
  }

  const showSectionTitle = !focusProvider;

  return (
    <div className="flex flex-col gap-3">
      {showSectionTitle ? (
        <Text className="text-sm font-medium text-content-2">
          {t(I18nKey.PROJECT_MANAGEMENT$TITLE)}
        </Text>
      ) : null}

      {/* Jira Cloud + Linear are multi-workspace SaaS integrations and keep the
          compact row + modal. Their config is short. */}
      {(jiraEnabled || linearEnabled) && (
        <div
          className={cn(
            settingsListContainerClassName,
            settingsListDividerClassName,
          )}
        >
          {jiraEnabled && (
            <IntegrationRow
              platform="jira"
              platformName="Jira Cloud"
              data-testid="jira-integration-row"
            />
          )}
          {linearEnabled && (
            <IntegrationRow
              platform="linear"
              platformName="Linear"
              data-testid="linear-integration-row"
            />
          )}
        </div>
      )}

      {/* Jira DC is a single-server integration with more setup, so it gets a
          full-width on-page panel (like the Git webhook managers) instead of a
          cramped modal. */}
      {jiraDcEnabled && <JiraDcIntegrationPanel />}
    </div>
  );
}
