import { useQuery } from "@tanstack/react-query";
import React from "react";
import { useConfig } from "./use-config";
import UserService from "#/api/user-service/user-service.api";
import { useShouldShowGitFeatures } from "#/hooks/use-should-show-git-features";
import { useLogout } from "../mutation/use-logout";

export const useGitUser = () => {
  const { data: config } = useConfig();
  const logout = useLogout();

  // Use the Git-specific hook to determine if we should fetch user data.
  // This requires a Git provider to be configured, not just authentication.
  const shouldFetchUser = useShouldShowGitFeatures();

  const user = useQuery({
    queryKey: ["user"],
    queryFn: UserService.getUser,
    enabled: shouldFetchUser,
    retry: false,
    staleTime: 1000 * 60 * 5, // 5 minutes
    gcTime: 1000 * 60 * 15, // 15 minutes
  });

  // Legacy saas login brokers the git token via Keycloak, so re-login fixes a
  // 401. With OAuth v2 the session IdP and git links are independent: re-login
  // would not refresh the token and would loop. OSS has no refresh mechanism.
  const isOAuthV2Login = !!config?.feature_flags?.enable_oauth_v2_login;
  React.useEffect(() => {
    if (
      user?.error?.response?.status === 401 &&
      config?.app_mode === "saas" &&
      !isOAuthV2Login
    ) {
      logout.mutate();
    }
  }, [user.status, config?.app_mode, isOAuthV2Login]);

  return user;
};
