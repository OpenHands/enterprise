import React from "react";
import { useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { displaySuccessToast } from "#/utils/custom-toast-handlers";
import { reportInvitationFailure } from "#/utils/report-invitation-failure";

const INVITATION_TOKEN_KEY = "openhands_invitation_token";

/** What the sign-in callback adds to its redirect after trying the invitation. */
const CALLBACK_OUTCOME_PARAMS = [
  "invitation_success",
  "invitation_expired",
  "invitation_invalid",
  "invitation_error",
  "already_member",
  "email_mismatch",
] as const;

interface UseInvitationOptions {
  /**
   * Tell the user how the sign-in callback's invitation attempt ended. Only
   * one mounted instance should, or each would show the same message.
   */
  reportOutcome?: boolean;
}

interface UseInvitationReturn {
  /** The invitation token, if present */
  invitationToken: string | null;
  /** Whether there is an active invitation */
  hasInvitation: boolean;
  /** Clear the stored invitation token */
  clearInvitation: () => void;
  /** Build OAuth state data including invitation token if present */
  buildOAuthStateData: (
    baseStateData: Record<string, string>,
  ) => Record<string, string>;
}

/**
 * Hook to manage organization invitation tokens during the login flow.
 *
 * This hook:
 * 1. Reads invitation_token from URL query params on mount
 * 2. Persists the token in localStorage (survives page refresh and works across tabs)
 * 3. Provides the token for inclusion in OAuth state
 * 4. Provides cleanup method after successful authentication
 *
 * The invitation token flow:
 * 1. User clicks invitation link → /api/invitations/accept?token=xxx
 * 2. Backend redirects to /login?invitation_token=xxx
 * 3. This hook captures token and stores in localStorage
 * 4. When user clicks login button, token is included in OAuth state
 * 5. After auth callback processes invitation, frontend clears the token
 *
 * Note: localStorage is used instead of sessionStorage to support scenarios where
 * the user opens the email verification link in a new tab/browser window.
 */
export function useInvitation({
  reportOutcome = false,
}: UseInvitationOptions = {}): UseInvitationReturn {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const reportedOutcomeRef = React.useRef<string | null>(null);
  const [invitationToken, setInvitationToken] = React.useState<string | null>(
    () => {
      // Initialize from localStorage (persists across tabs and page refreshes)
      if (typeof window !== "undefined") {
        return localStorage.getItem(INVITATION_TOKEN_KEY);
      }
      return null;
    },
  );

  // Capture invitation token from URL and persist to localStorage
  // This only runs on the login page where the hook is used
  React.useEffect(() => {
    const tokenFromUrl = searchParams.get("invitation_token");

    if (tokenFromUrl) {
      // Store in localStorage for persistence across tabs and refreshes
      localStorage.setItem(INVITATION_TOKEN_KEY, tokenFromUrl);
      setInvitationToken(tokenFromUrl);

      // Remove token from URL to clean up (prevents token exposure in browser history)
      const newSearchParams = new URLSearchParams(searchParams);
      newSearchParams.delete("invitation_token");
      setSearchParams(newSearchParams, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  // Clear invitation token when invitation flow completes (success or failure)
  // These query params are set by the backend after processing the invitation
  React.useEffect(() => {
    const outcome = CALLBACK_OUTCOME_PARAMS.find((param) =>
      searchParams.has(param),
    );

    if (outcome) {
      // A sign-in with the wrong account leaves the invitation pending, so
      // keep the token: the signed-in accept retries it and explains the
      // mismatch, and signing in with the invited email can still use it.
      if (outcome !== "email_mismatch") {
        localStorage.removeItem(INVITATION_TOKEN_KEY);
        setInvitationToken(null);
      }

      if (reportOutcome && reportedOutcomeRef.current !== outcome) {
        reportedOutcomeRef.current = outcome;
        if (outcome === "invitation_success") {
          displaySuccessToast(t(I18nKey.ORG$INVITATION_ACCEPTED));
        } else if (outcome !== "email_mismatch") {
          reportInvitationFailure(
            outcome === "invitation_error" ? null : outcome,
            t,
          );
        }
      }

      // Remove invitation params from URL to clean up
      const newSearchParams = new URLSearchParams(searchParams);
      newSearchParams.delete("invitation_success");
      newSearchParams.delete("invitation_expired");
      newSearchParams.delete("invitation_invalid");
      newSearchParams.delete("invitation_error");
      newSearchParams.delete("already_member");
      newSearchParams.delete("email_mismatch");
      setSearchParams(newSearchParams, { replace: true });
    }
  }, [searchParams, setSearchParams, reportOutcome, t]);

  const clearInvitation = React.useCallback(() => {
    localStorage.removeItem(INVITATION_TOKEN_KEY);
    setInvitationToken(null);
  }, []);

  const buildOAuthStateData = React.useCallback(
    (baseStateData: Record<string, string>): Record<string, string> => {
      const stateData = { ...baseStateData };

      // Include invitation token in state if present
      if (invitationToken) {
        stateData.invitation_token = invitationToken;
      }

      return stateData;
    },
    [invitationToken],
  );

  return {
    invitationToken,
    hasInvitation: invitationToken !== null,
    clearInvitation,
    buildOAuthStateData,
  };
}
