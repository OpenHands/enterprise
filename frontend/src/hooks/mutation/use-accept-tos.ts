import type { UseMutationResult, DefaultError } from "@tanstack/react-query";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { usePostHog } from "posthog-js/react";
import { useNavigate } from "react-router";
import { useConfig } from "../query/use-config";
import AuthService from "#/api/auth-service/auth-service.api";
import { handleCaptureConsent } from "#/utils/handle-capture-consent";
import { navigateOrHardRedirect } from "#/utils/cross-app-redirect";

interface AcceptTosVariables {
  redirectUrl: string;
}

export const useAcceptTos = (): UseMutationResult<
  Awaited<ReturnType<typeof AuthService.acceptTos>>,
  DefaultError,
  AcceptTosVariables,
  unknown
> => {
  const posthog = usePostHog();
  const navigate = useNavigate();
  const client = useQueryClient();
  const { data: config } = useConfig();

  return useMutation({
    mutationFn: async ({ redirectUrl }: AcceptTosVariables) => {
      // Set consent for analytics
      handleCaptureConsent(posthog, true);

      // Call the API to record TOS acceptance in the database
      return AuthService.acceptTos(redirectUrl);
    },
    onSuccess: async (response, { redirectUrl }) => {
      if (config?.auth_mode === "native") {
        client.setQueryData(["user", "authenticated", "saas", "native"], {
          authenticated: true,
          acceptedTos: true,
        });
        await client.invalidateQueries({ queryKey: ["user", "authenticated"] });
      }
      // Get the redirect URL from the response
      const finalRedirectUrl = response.data.redirect_url || redirectUrl;

      // Check if the redirect URL is an external URL (starts with http or https)
      if (
        finalRedirectUrl.startsWith("http://") ||
        finalRedirectUrl.startsWith("https://")
      ) {
        // For external URLs, redirect using window.location
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
