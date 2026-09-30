import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { Text, Paragraph } from "#/ui/typography";
import { useGitConversationRouting } from "#/hooks/organizations/use-git-conversation-routing";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { cn } from "#/utils/utils";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
} from "#/utils/settings-list-classes";
import { GitOrgRow } from "./git-org-row";

export function GitConversationRouting() {
  const { t } = useTranslation();
  const { orgs, claimOrg, disconnectOrg, isLoading } =
    useGitConversationRouting();

  return (
    <div
      data-testid="git-conversation-routing"
      className="flex w-full flex-col gap-4"
    >
      <div className="flex flex-col gap-1">
        <Text className="text-lg font-medium text-white">
          {t(I18nKey.ORG$GIT_CONVERSATION_ROUTING)}
        </Text>

        <Paragraph className="text-sm font-normal leading-5 text-tertiary-light">
          {t(I18nKey.ORG$GIT_CONVERSATION_ROUTING_DESCRIPTION)}
        </Paragraph>
      </div>

      {isLoading && (
        <div className="flex justify-center py-4">
          <LoadingSpinner size="small" />
        </div>
      )}

      {!isLoading && orgs.length > 0 && (
        <div
          className={cn(
            settingsListContainerClassName,
            settingsListDividerClassName,
          )}
        >
          {orgs.map((org) => (
            <GitOrgRow
              key={org.id}
              org={org}
              onClaim={claimOrg}
              onDisconnect={disconnectOrg}
            />
          ))}
        </div>
      )}

      {!isLoading && orgs.length === 0 && (
        <Paragraph className="py-4 text-center text-sm text-[var(--oh-muted)]">
          {t(I18nKey.ORG$NO_GIT_ORGANIZATIONS)}
        </Paragraph>
      )}
    </div>
  );
}
