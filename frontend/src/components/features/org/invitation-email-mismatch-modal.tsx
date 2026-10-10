import { useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { useLogout } from "#/hooks/mutation/use-logout";
import { useMe } from "#/hooks/query/use-me";
import { useInvitation } from "#/hooks/use-invitation";
import { I18nKey } from "#/i18n/declaration";
import { useInvitationEmailMismatchStore } from "#/stores/invitation-email-mismatch-store";

/**
 * Explains an invitation refused because the signed-in account's email is not
 * the invited one. Signing out keeps the invitation, so signing in with the
 * invited email still applies it; closing the dialog discards it.
 */
export function InvitationEmailMismatchModal() {
  const { t } = useTranslation();
  const close = useInvitationEmailMismatchStore((state) => state.close);
  const { data: me } = useMe();
  const { clearInvitation } = useInvitation();
  const { mutate: logout, isPending: isSigningOut } = useLogout();
  const email = me?.email;

  return (
    <OrgModal
      testId="invitation-email-mismatch-modal"
      title={t(I18nKey.ORG$INVITATION_EMAIL_MISMATCH_TITLE)}
      description={
        email
          ? t(I18nKey.ORG$INVITATION_EMAIL_MISMATCH_BODY, { email })
          : t(I18nKey.ORG$INVITATION_EMAIL_MISMATCH_BODY_NO_EMAIL)
      }
      primaryButtonText={t(I18nKey.ORG$INVITATION_SIGN_IN_WITH_ANOTHER_ACCOUNT)}
      primaryButtonTestId="invitation-email-mismatch-sign-in"
      onPrimaryClick={() => logout()}
      secondaryButtonText={t(I18nKey.BUTTON$CLOSE)}
      secondaryButtonTestId="invitation-email-mismatch-close"
      onClose={() => {
        clearInvitation();
        close();
      }}
      isLoading={isSigningOut}
    />
  );
}
