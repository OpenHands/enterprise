import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router";
import { useAuthentication } from "./use-authentication";
import { useIsAuthed } from "./query/use-is-authed";
import { LoginMethod, setLoginMethod } from "#/utils/local-storage";
import { useConfig } from "./query/use-config";
import { navigateOrHardRedirect } from "#/utils/cross-app-redirect";

/**
 * Hook to handle authentication callback and set login method after successful authentication
 */
export const useAuthCallback = (): void => {
  const location = useLocation();
  const { data: isAuthed, isLoading: isAuthLoading } = useIsAuthed();
  const authentication = useAuthentication();
  const { data: config } = useConfig();
  const navigate = useNavigate();

  useEffect(() => {
    // Only run in SAAS mode
    if (!authentication.providerLoginEnabled(config?.app_mode)) {
      return;
    }

    // Wait for auth to load
    if (isAuthLoading) {
      return;
    }

    // Only process callback if authentication was successful
    if (!isAuthed) {
      return;
    }

    // Check if we have a login_method query parameter
    const searchParams = new URLSearchParams(location.search);
    const loginMethod = Object.values(LoginMethod).find(
      (method) => method === searchParams.get("login_method"),
    );
    const returnTo = searchParams.get("returnTo");

    // Set the login method if it's valid
    if (loginMethod !== undefined) {
      setLoginMethod(loginMethod);

      // Clean up the URL by removing auth-related parameters
      searchParams.delete("login_method");
      searchParams.delete("returnTo");

      // Determine where to navigate after authentication
      let destination = "/";
      if (returnTo && returnTo !== "/login") {
        destination = returnTo;
      } else if (location.pathname !== "/login" && location.pathname !== "/") {
        destination = location.pathname;
      }

      const remainingParams = searchParams.toString();
      const finalUrl = remainingParams
        ? `${destination}?${remainingParams}`
        : destination;

      navigateOrHardRedirect(navigate, finalUrl, { replace: true });
    }
  }, [
    isAuthed,
    isAuthLoading,
    location.search,
    location.pathname,
    config?.app_mode,
    authentication,
    navigate,
  ]);
};
