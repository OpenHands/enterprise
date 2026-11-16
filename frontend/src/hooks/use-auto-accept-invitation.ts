import React from "react";
import { AxiosError } from "axios";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useIsAuthed } from "#/hooks/query/use-is-authed";
import { useIsOnIntermediatePage } from "#/hooks/use-is-on-intermediate-page";
import { useInvitation } from "#/hooks/use-invitation";
import {
  useAcceptInvitation,
  getInvitationErrorCode,
} from "#/hooks/mutation/use-accept-invitation";
import { useSwitchOrganization } from "#/hooks/mutation/use-switch-organization";
import { displaySuccessToast } from "#/utils/custom-toast-handlers";
import { reportInvitationFailure } from "#/utils/report-invitation-failure";

/**
 * Accept a pending invitation token automatically once the user is
 * authenticated.
 *
 * Clicking the invitation link is the user's consent — an extra
 * confirm/cancel dialog added no security (the token is bound to the
 * invited email) and its cancel didn't actually decline anything, so the
 * token is simply submitted as soon as a session exists and the outcome is
 * reported via a toast, or a dialog when the account's email is not the
 * invited one.
 */
export function useAutoAcceptInvitation() {
  const { t } = useTranslation();
  const { data: isAuthed } = useIsAuthed();
  const isOnIntermediatePage = useIsOnIntermediatePage();
  const { invitationToken, clearInvitation } = useInvitation();
  const { mutate: acceptInvitation } = useAcceptInvitation();
  const { mutate: switchOrganization } = useSwitchOrganization();
  const attemptedTokenRef = React.useRef<string | null>(null);

  React.useEffect(() => {
    if (!isAuthed || !invitationToken || isOnIntermediatePage) return;
    if (attemptedTokenRef.current === invitationToken) return;
    attemptedTokenRef.current = invitationToken;

    acceptInvitation(
      { token: invitationToken },
      {
        onSuccess: (data) => {
          clearInvitation();
          displaySuccessToast(
            t(I18nKey.ORG$INVITATION_ACCEPTED_SUCCESS, {
              orgName: data.org_name,
            }),
          );
          switchOrganization({
            orgId: data.org_id,
            orgName: data.org_name,
            isPersonal: false,
          });
        },
        onError: (error) => {
          const errorCode = getInvitationErrorCode(
            error as AxiosError<{ detail: string }>,
          );
          // A wrong-account sign-in leaves the invitation pending; the
          // mismatch dialog decides whether to keep it for the next sign-in.
          if (errorCode !== "email_mismatch") {
            clearInvitation();
          }
          reportInvitationFailure(errorCode, t);
        },
      },
    );
  }, [
    isAuthed,
    invitationToken,
    isOnIntermediatePage,
    acceptInvitation,
    switchOrganization,
    clearInvitation,
    t,
  ]);
}
