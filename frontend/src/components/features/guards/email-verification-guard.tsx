import React from "react";
import { useLocation, useNavigate } from "react-router";
import { useSettings } from "#/hooks/query/use-settings";
import { useIsEmailVerificationBlocking } from "#/hooks/use-is-email-verification-blocking";

/**
 * Restricts an unverified user to the /settings/user page when the server
 * requires verified emails.
 */
export function EmailVerificationGuard({
  children,
}: {
  children: React.ReactNode;
}) {
  const { isLoading } = useSettings();
  const isBlocked = useIsEmailVerificationBlocking();
  const navigate = useNavigate();
  const { pathname } = useLocation();

  React.useEffect(() => {
    if (isLoading) return;

    if (isBlocked && pathname !== "/settings/user") {
      navigate("/settings/user", { replace: true });
    }
  }, [isBlocked, pathname, navigate, isLoading]);

  return children;
}
