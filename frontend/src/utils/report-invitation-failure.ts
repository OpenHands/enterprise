import type { TFunction } from "i18next";
import { I18nKey } from "#/i18n/declaration";
import { useInvitationEmailMismatchStore } from "#/stores/invitation-email-mismatch-store";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";

/**
 * Tell the user why an invitation was not applied. The accept API and the
 * sign-in callback use the same codes. A sign-in with the wrong account opens
 * a dialog instead of a toast, because the user has to act on it.
 */
export function reportInvitationFailure(code: string | null, t: TFunction) {
  switch (code) {
    case "already_member":
      // The invitation was already applied (e.g. accepted on the user's
      // behalf at sign-in) — success from their perspective.
      displaySuccessToast(t(I18nKey.ORG$ALREADY_MEMBER));
      break;
    case "invitation_expired":
      displayErrorToast(t(I18nKey.ORG$INVITATION_EXPIRED));
      break;
    case "email_mismatch":
      useInvitationEmailMismatchStore.getState().open();
      break;
    case "invitation_invalid":
      displayErrorToast(t(I18nKey.ORG$INVITATION_INVALID));
      break;
    default:
      displayErrorToast(t(I18nKey.ORG$INVITATION_ACCEPT_ERROR));
  }
}
