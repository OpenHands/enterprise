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
 *
 * The email (and role -- always ``member`` here) are already known, so
 * there's nothing for the admin to configure or confirm: the link is minted
 * as soon as this mounts, and only the result (with its copy button) is
 * ever shown. On error the toast from `useCreateSignupLink` is enough
 * feedback, so this just closes rather than leaving an empty modal up.
 */
export function CreatePasswordResetLinkModal({
  email,
  onClose,
}: CreatePasswordResetLinkModalProps) {
  const { t } = useTranslation();
  const { mutate: createSignupLink } = useCreateSignupLink(
    I18nKey.ORG$CREATE_PASSWORD_RESET_LINK_ERROR,
  );
  const [link, setLink] = React.useState<string | null>(null);
  const hasRequestedRef = React.useRef(false);

  React.useEffect(() => {
    if (hasRequestedRef.current) return;
    hasRequestedRef.current = true;
    createSignupLink(
      { email, role: "member" },
      {
        onSuccess: (data) => setLink(data.url),
        onError: () => onClose(),
      },
    );
  }, [createSignupLink, email, onClose]);

  if (!link) {
    return null;
  }

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
        <CopyInviteLinkButton
          inviteUrl={link}
          label={I18nKey.ORG$COPY_LINK}
          copiedLabel={I18nKey.ORG$LINK_COPIED}
        />
      </div>
    </OrgModal>
  );
}
