import { useTranslation } from "react-i18next";
import { AgentProfileSummary } from "#/api/agent-profiles-service/agent-profiles-service.api";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";
import { AgentProfileRow } from "./agent-profile-row";

interface AgentProfilesBodyProps {
  isLoading: boolean;
  loadError: Error | null;
  profiles: AgentProfileSummary[];
  activeId: string | null;
  onActivate: (profile: AgentProfileSummary) => void;
  onEdit: (profile: AgentProfileSummary) => void;
  onDelete: (profile: AgentProfileSummary) => void;
  isActivating: boolean;
  /** True when the viewer may edit and delete the organization's profiles. */
  canManageOrg?: boolean;
}

export function AgentProfilesBody({
  isLoading,
  loadError,
  profiles,
  activeId,
  onActivate,
  onEdit,
  onDelete,
  isActivating,
  canManageOrg = false,
}: AgentProfilesBodyProps) {
  const { t } = useTranslation();

  if (isLoading) {
    return (
      <div className="flex justify-center p-4">
        <LoadingSpinner size="large" />
      </div>
    );
  }
  if (loadError) {
    return (
      <Typography.Paragraph className="text-sm text-red-400">
        {t(I18nKey.SETTINGS$PROFILES_LOAD_ERROR)}
      </Typography.Paragraph>
    );
  }
  if (profiles.length === 0) {
    return (
      <Typography.Paragraph
        testId="agent-profiles-empty"
        className="text-sm text-[var(--oh-muted)] italic"
      >
        {t(I18nKey.SETTINGS$AGENT_PROFILES_EMPTY)}
      </Typography.Paragraph>
    );
  }
  return (
    <div
      className={cn(
        settingsListContainerClassName,
        settingsListDividerClassName,
      )}
    >
      {profiles.map((profile) => (
        <AgentProfileRow
          key={`${profile.scope}:${profile.id ?? profile.name}`}
          profile={profile}
          isActive={!!profile.id && profile.id === activeId}
          onActivate={onActivate}
          onEdit={onEdit}
          onDelete={onDelete}
          isActivating={isActivating}
          canManageOrg={canManageOrg}
        />
      ))}
    </div>
  );
}
