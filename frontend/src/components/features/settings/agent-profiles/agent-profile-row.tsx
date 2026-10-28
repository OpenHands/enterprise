import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AgentProfileSummary } from "#/api/agent-profiles-service/agent-profiles-service.api";
import { ProfileActionsMenu } from "#/components/features/settings/profile-actions-menu";
import { I18nKey } from "#/i18n/declaration";
import ThreeDotsVerticalIcon from "#/icons/three-dots-vertical.svg?react";
import { Typography } from "#/ui/typography";
import {
  settingsListIconActionButtonClassName,
  settingsListRowClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";

interface AgentProfileRowProps {
  profile: AgentProfileSummary;
  isActive: boolean;
  onActivate: (profile: AgentProfileSummary) => void;
  onEdit: (profile: AgentProfileSummary) => void;
  onDelete: (profile: AgentProfileSummary) => void;
  isActivating: boolean;
  /** True when the viewer may edit and delete the organization's profiles. */
  canManageOrg?: boolean;
}

export function AgentProfileRow({
  profile,
  isActive,
  onActivate,
  onEdit,
  onDelete,
  isActivating,
  canManageOrg = false,
}: AgentProfileRowProps) {
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  // The member's own profiles are always editable here. The org's shared ones
  // (listed in their own section) are managed by admins; other members can
  // just set one as their default. A legacy ACP org profile can only be
  // deleted: the org editor supports the OpenHands agent alone.
  const isOrgProfile = profile.scope !== "personal";
  const canDelete = !isOrgProfile || canManageOrg;
  const canEdit = canDelete && !(isOrgProfile && profile.agent_kind === "acp");
  // Secondary label: the referenced LLM profile (OpenHands) or the ACP kind.
  const secondary =
    profile.agent_kind === "openhands"
      ? profile.llm_profile_ref
      : t(I18nKey.SETTINGS$AGENT_TYPE_ACP);

  return (
    <div
      data-testid="agent-profile-row"
      className={cn(
        settingsListRowClassName,
        "h-auto items-start justify-between gap-3 py-3 sm:items-center",
      )}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
        <div className="flex min-w-0 max-w-full items-center gap-2">
          <Typography.Text
            className="min-w-0 truncate font-medium text-white"
            title={profile.name}
          >
            {profile.name}
          </Typography.Text>
          {isActive && (
            <Typography.Text
              className="shrink-0 whitespace-nowrap rounded-full bg-primary px-2 py-0.5 text-xs font-semibold text-[var(--oh-color-base)]"
              testId="agent-profile-active-badge"
            >
              {t(I18nKey.SETTINGS$PROFILE_ACTIVE_BADGE)}
            </Typography.Text>
          )}
        </div>
        {secondary ? (
          <Typography.Text
            className="min-w-0 max-w-full truncate text-sm text-[var(--oh-muted)]"
            title={secondary}
          >
            {secondary}
          </Typography.Text>
        ) : null}
      </div>
      <div className="relative shrink-0">
        <button
          ref={triggerRef}
          type="button"
          onClick={() => setMenuOpen((open) => !open)}
          aria-label={t(I18nKey.SETTINGS$PROFILE_MENU)}
          className={settingsListIconActionButtonClassName}
          data-testid="agent-profile-menu-trigger"
        >
          <ThreeDotsVerticalIcon width={16} height={16} />
        </button>
        {menuOpen && (
          <ProfileActionsMenu
            anchorRef={triggerRef}
            onEdit={canEdit ? () => onEdit(profile) : undefined}
            onSetActive={() => onActivate(profile)}
            onDelete={canDelete ? () => onDelete(profile) : undefined}
            isActive={isActive}
            isActivating={isActivating}
            onClose={() => setMenuOpen(false)}
          />
        )}
      </div>
    </div>
  );
}
