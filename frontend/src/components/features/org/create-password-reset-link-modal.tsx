import React from "react";
import { useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { I18nKey } from "#/i18n/declaration";
import { CopyInviteLinkButton } from "#/components/features/org/copy-invite-link-button";
import { useCreateSignupLink } from "#/hooks/mutation/use-create-signup-link";

interface CreatePasswordResetLinkModalProps {
  email: string;
  onClose: () => void;
}

/**
 * Mints an admin-issued sign-up link (``POST /api/idp/signup-links``) for an
 * *existing* user's email, with the org-independent ``member`` role and no
 * ``orgId``. The backend's ``idp_invite_accept`` resolves the email to the
 * existing account and resets its password -- this is this IDP's only
 * password-reset path (see ``server.routes.idp``'s module docstring) -- and,
 * since the recipient is already a member wherever they need to be, the
 * org-scoped role claim is a no-op rather than granting anything new.
 *
 * Only rendered when ``enable_integrated_idp`` is on (see
 * ``option.types.ts``), since the generated link is otherwise meaningless --
 * there is no local-password login to reset into.
 */
export function CreatePasswordResetLinkModal({
  email,
  onClose,
}: CreatePasswordResetLinkModalProps) {
  const { t } = useTranslation();
  const { mutate: createSignupLink, isPending } = useCreateSignupLink(
    I18nKey.ORG$CREATE_PASSWORD_RESET_LINK_ERROR,
  );
  const [link, setLink] = React.useState<string | null>(null);

  const handleSubmit = () => {
    createSignupLink(
      { email, role: "member" },
      { onSuccess: (data) => setLink(data.url) },
    );
  };

  if (link) {
    return (
      <OrgModal
        testId="password-reset-link-result-modal"
        title={t(I18nKey.ORG$PASSWORD_RESET_LINK_CREATED)}
        description={t(I18nKey.ORG$PASSWORD_RESET_LINK_CREATED_DESCRIPTION)}
        primaryButtonText={t(I18nKey.BUTTON$CLOSE)}
        onPrimaryClick={() => onClose()}
        onClose={onClose}
        hideSecondaryButton
      >
        <div
          className="flex items-center justify-between gap-2 text-sm"
          data-testid="password-reset-link-result"
        >
          <span className="truncate">{email}</span>
          <CopyInviteLinkButton inviteUrl={link} />
        </div>
      </OrgModal>
    );
  }

  return (
    <OrgModal
      testId="create-password-reset-link-modal"
      title={t(I18nKey.ORG$CREATE_PASSWORD_RESET_LINK)}
      description={t(I18nKey.ORG$CREATE_PASSWORD_RESET_LINK_DESCRIPTION, {
        email,
      })}
      primaryButtonText={t(I18nKey.BUTTON$CREATE)}
      onPrimaryClick={handleSubmit}
      onClose={onClose}
      isLoading={isPending}
    />
  );
}
