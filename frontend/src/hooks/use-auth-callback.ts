import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router";
import { useIsAuthed } from "./query/use-is-authed";
import { LoginMethod, setLoginMethod } from "#/utils/local-storage";
import { useConfig } from "./query/use-config";
import { navigateOrHardRedirect } from "#/utils/cross-app-redirect";

/**
 * Hook to handle authentication callback and set login method after successful authentication
 */
export const useAuthCallback = () => {
  const location = useLocation();
  const { data: isAuthed, isLoading: isAuthLoading } = useIsAuthed();
  const { data: config } = useConfig();
  const navigate = useNavigate();

  useEffect(() => {
    if (config?.app_mode !== "saas") {
      return;
    }

    if (isAuthLoading) {
      return;
    }

    if (!isAuthed) {
      return;
    }

    const searchParams = new URLSearchParams(location.search);
    const loginMethod = searchParams.get("login_method");
    const returnTo = searchParams.get("returnTo");

    if (Object.values(LoginMethod).includes(loginMethod as LoginMethod)) {
      setLoginMethod(loginMethod as LoginMethod);

      searchParams.delete("login_method");
      searchParams.delete("returnTo");

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
    navigate,
  ]);
};
