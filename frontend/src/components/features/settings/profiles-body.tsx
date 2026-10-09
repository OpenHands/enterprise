import { useTranslation } from "react-i18next";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { ProfileRow } from "#/components/features/settings/profile-row";
import { LlmProfileSummary } from "#/api/settings-service/profiles-service.api";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";

interface ProfilesBodyProps {
  isLoading: boolean;
  loadError: Error | null;
  profiles: LlmProfileSummary[];
  active: string | null;
  /**
   * Display name per provider-connection id. When non-empty, profiles are
   * grouped under their connection's name so models sharing a connection are
   * visually clustered. Empty (the default) renders a flat list.
   */
  connectionNamesById?: Record<string, string>;
  onActivate: (name: string) => void;
  onEdit: (profile: LlmProfileSummary) => void;
  onRename: (profile: LlmProfileSummary) => void;
  onDelete: (profile: LlmProfileSummary) => void;
  isActivating: boolean;
  canManage?: boolean;
}

interface ProfileGroup {
  /** Connection id, or null for profiles with no provider connection. */
  connectionId: string | null;
  label: string | null;
  profiles: LlmProfileSummary[];
}

/**
 * Bucket profiles by their `provider_connection_id`, preserving input order
 * within each group and ordering groups by first appearance. Unlinked profiles
 * collect under a trailing `null` group.
 */
export function groupProfilesByConnection(
  profiles: LlmProfileSummary[],
  connectionNamesById: Record<string, string>,
): ProfileGroup[] {
  const groups = new Map<string, ProfileGroup>();
  const unlinked: ProfileGroup = {
    connectionId: null,
    label: null,
    profiles: [],
  };

  for (const profile of profiles) {
    const connectionId = profile.provider_connection_id ?? null;
    if (!connectionId) {
      unlinked.profiles.push(profile);
    } else {
      let group = groups.get(connectionId);
      if (!group) {
        group = {
          connectionId,
          label: connectionNamesById[connectionId] ?? connectionId,
          profiles: [],
        };
        groups.set(connectionId, group);
      }
      group.profiles.push(profile);
    }
  }

  const linkedGroups = [...groups.values()];
  return unlinked.profiles.length > 0
    ? [...linkedGroups, unlinked]
    : linkedGroups;
}

export function ProfilesBody({
  isLoading,
  loadError,
  profiles,
  active,
  connectionNamesById = {},
  onActivate,
  onEdit,
  onRename,
  onDelete,
  isActivating,
  canManage = true,
}: ProfilesBodyProps) {
  const { t } = useTranslation();

  const renderRow = (profile: LlmProfileSummary) => (
    <ProfileRow
      key={profile.name}
      profile={profile}
      isActive={profile.name === active}
      onActivate={onActivate}
      onEdit={onEdit}
      onRename={onRename}
      onDelete={onDelete}
      isActivating={isActivating}
      canManage={canManage}
    />
  );

  const listClassName = cn(
    settingsListContainerClassName,
    settingsListDividerClassName,
  );

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
      <Typography.Paragraph className="text-sm text-[var(--oh-muted)] italic">
        {t(I18nKey.SETTINGS$PROFILES_EMPTY)}
      </Typography.Paragraph>
    );
  }

  // Group only when at least one profile links to a connection; otherwise
  // (nothing linked) render the flat list unchanged.
  const hasLinkedProfiles = profiles.some((p) => p.provider_connection_id);
  if (!hasLinkedProfiles) {
    return <div className={listClassName}>{profiles.map(renderRow)}</div>;
  }

  const groups = groupProfilesByConnection(profiles, connectionNamesById);
  return (
    <div className="flex flex-col gap-4">
      {groups.map((group) => (
        <div
          key={group.connectionId ?? "__unlinked__"}
          className="flex flex-col gap-2"
        >
          <h3
            data-testid="profile-group-header"
            className="text-xs font-medium uppercase tracking-wide text-[var(--oh-muted)]"
          >
            {group.label ?? t(I18nKey.SETTINGS$PROFILES_UNGROUPED)}
          </h3>
          <div className={listClassName}>{group.profiles.map(renderRow)}</div>
        </div>
      ))}
    </div>
  );
}
