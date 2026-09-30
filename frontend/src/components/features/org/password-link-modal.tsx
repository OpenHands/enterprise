import { useTranslation } from "react-i18next";
import { CopyInviteLinkButton } from "#/components/features/org/copy-invite-link-button";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { I18nKey } from "#/i18n/declaration";
import { PasswordLinkResponse } from "#/types/org";

interface PasswordLinkModalProps {
  link: PasswordLinkResponse;
  email: string;
  onClose: () => void;
}

export function PasswordLinkModal({
  link,
  email,
  onClose,
}: PasswordLinkModalProps) {
  const { t } = useTranslation();

  return (
    <OrgModal
      testId="password-link-modal"
      title={t(I18nKey.ORG$PASSWORD_LINK_TITLE)}
      description={t(I18nKey.ORG$PASSWORD_LINK_DESCRIPTION)}
      primaryButtonText={t(I18nKey.BUTTON$CLOSE)}
      onPrimaryClick={onClose}
      onClose={onClose}
      hideSecondaryButton
    >
      <div className="flex flex-col gap-3 text-sm">
        <span className="truncate text-muted">{email}</span>
        <div className="flex items-center justify-between gap-3 rounded border border-tertiary p-3">
          <span className="truncate font-mono text-xs">{link.url}</span>
          <CopyInviteLinkButton inviteUrl={link.url} />
        </div>
        <p className="text-xs text-tertiary-alt">
          {t(I18nKey.ORG$PASSWORD_LINK_EXPIRY)}
        </p>
      </div>
    </OrgModal>
  );
}
