import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ProfileActionsMenu } from "#/components/features/settings/profile-actions-menu";
import { LlmProfileSummary } from "#/api/settings-service/profiles-service.api";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";
import ThreeDotsVerticalIcon from "#/icons/three-dots-vertical.svg?react";
import {
  settingsListIconActionButtonClassName,
  settingsListRowClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";

interface ProfileRowProps {
  profile: LlmProfileSummary;
  isActive: boolean;
  onActivate: (name: string) => void;
  onEdit: (profile: LlmProfileSummary) => void;
  onRename: (profile: LlmProfileSummary) => void;
  onDelete: (profile: LlmProfileSummary) => void;
  isActivating: boolean;
  canManage?: boolean;
}

export function ProfileRow({
  profile,
  isActive,
  onActivate,
  onEdit,
  onRename,
  onDelete,
  isActivating,
  canManage = true,
}: ProfileRowProps) {
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  return (
    <div
      data-testid="profile-row"
      className={cn(settingsListRowClassName, "justify-between gap-3")}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
        <Typography.Text
          className="min-w-0 max-w-full truncate font-medium text-white"
          title={profile.name}
        >
          {profile.name}
        </Typography.Text>
        {profile.model ? (
          <Typography.Text
            className="min-w-0 max-w-full truncate text-sm text-[var(--oh-muted)]"
            title={profile.model}
          >
            {profile.model}
          </Typography.Text>
        ) : null}
        {isActive && (
          <Typography.Text
            className="self-start whitespace-nowrap rounded-full bg-primary px-2 py-0.5 text-xs font-semibold text-[var(--oh-color-base)] sm:self-auto"
            testId="profile-active-badge"
          >
            {t(I18nKey.SETTINGS$PROFILE_ACTIVE_BADGE)}
          </Typography.Text>
        )}
      </div>
      {canManage ? (
        <div className="relative shrink-0">
          <button
            ref={triggerRef}
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            aria-label={t(I18nKey.SETTINGS$PROFILE_MENU)}
            className={settingsListIconActionButtonClassName}
            data-testid="profile-menu-trigger"
          >
            <ThreeDotsVerticalIcon width={16} height={16} />
          </button>
          {menuOpen && (
            <ProfileActionsMenu
              anchorRef={triggerRef}
              onEdit={() => onEdit(profile)}
              onRename={() => onRename(profile)}
              onSetActive={() => onActivate(profile.name)}
              onDelete={() => onDelete(profile)}
              isActive={isActive}
              isActivating={isActivating}
              onClose={() => setMenuOpen(false)}
            />
          )}
        </div>
      ) : null}
    </div>
  );
}
