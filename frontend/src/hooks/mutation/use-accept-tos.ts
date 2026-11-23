import { useMutation } from "@tanstack/react-query";
import { usePostHog } from "posthog-js/react";
import { useNavigate } from "react-router";
import { openHands } from "#/api/open-hands-axios";
import { handleCaptureConsent } from "#/utils/handle-capture-consent";
import { navigateOrHardRedirect } from "#/utils/cross-app-redirect";
import { clearSignupEntry, getSignupEntry } from "#/utils/signup-attribution";

interface AcceptTosVariables {
  redirectUrl: string;
}

interface AcceptTosResponse {
  redirect_url?: string;
}

export const useAcceptTos = () => {
  const posthog = usePostHog();
  const navigate = useNavigate();

  return useMutation({
    mutationFn: async ({ redirectUrl }: AcceptTosVariables) =>
      openHands.post<AcceptTosResponse>("/api/accept_tos", {
        redirect_url: redirectUrl,
        ...(getSignupEntry() ?? {}),
      }),
    onSuccess: (response, { redirectUrl }) => {
      handleCaptureConsent(posthog, true);
      clearSignupEntry();
      const finalRedirectUrl = response.data.redirect_url || redirectUrl;

      if (
        finalRedirectUrl.startsWith("http://") ||
        finalRedirectUrl.startsWith("https://")
      ) {
        window.location.href = finalRedirectUrl;
      } else {
        navigateOrHardRedirect(navigate, finalRedirectUrl);
      }
    },
    onError: () => {
      window.location.href = "/";
    },
  });
};
