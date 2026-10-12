import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { OrganizationInvitation } from "#/types/org";
import { I18nKey } from "#/i18n/declaration";
import { CopyInviteLinkButton } from "#/components/features/org/copy-invite-link-button";
import { cn } from "#/utils/utils";
import {
  settingsListIconActionButtonClassName,
  settingsListRowClassName,
} from "#/utils/settings-list-classes";

interface PendingInvitationListItemProps {
  invitation: OrganizationInvitation;
  onRevoke: () => void;
  isRevoking: boolean;
}

/**
 * A pending invitation rendered as a row in the members list, styled to
 * match OrganizationMemberListItem with an "Invited" status chip. Instead
 * of the role menu it offers copy-invite-link and revoke actions.
 */
export function PendingInvitationListItem({
  invitation,
  onRevoke,
  isRevoking,
}: PendingInvitationListItemProps) {
  const { t } = useTranslation();

  return (
    <div
      data-testid="pending-invitation-item"
      className={cn(settingsListRowClassName, "justify-between")}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm font-normal leading-5 text-muted">
          {invitation.email}
        </span>
        <span className="shrink-0 rounded-lg border border-[var(--oh-border)] px-2 py-0.5 text-xs text-muted">
          {t(I18nKey.ORG$STATUS_INVITED)}
        </span>
      </div>

      <div className="flex shrink-0 items-center gap-2">
        <span className="text-xs font-normal leading-4 text-muted capitalize">
          {invitation.role}
        </span>
        {invitation.invite_url && (
          <CopyInviteLinkButton inviteUrl={invitation.invite_url} />
        )}
        <button
          type="button"
          data-testid="revoke-invitation-button"
          aria-label={t(I18nKey.ORG$REVOKE_INVITATION)}
          title={t(I18nKey.ORG$REVOKE_INVITATION)}
          onClick={onRevoke}
          disabled={isRevoking}
          className={cn(
            settingsListIconActionButtonClassName,
            "hover:text-danger disabled:cursor-not-allowed disabled:opacity-60",
          )}
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}
