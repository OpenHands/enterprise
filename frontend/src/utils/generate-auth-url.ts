import { getAuthReturnTo } from "./auth-redirect";

/** The backend owns provider authorization, callback URLs, and OAuth state. */
export const generateAuthUrl = (identityProvider: string, requestUrl: URL) => {
  const url = new URL("/api/auth/authorize", requestUrl.origin);
  url.searchParams.set("provider", identityProvider);
  const redirect =
    requestUrl.pathname === "/login"
      ? getAuthReturnTo(requestUrl.searchParams)
      : `${requestUrl.pathname}${requestUrl.search}`;
  url.searchParams.set("redirect_url", redirect);
  const invitationToken =
    requestUrl.searchParams.get("invitation_token") ||
    localStorage.getItem("openhands_invitation_token");
  if (invitationToken)
    url.searchParams.set("invitation_token", invitationToken);
  return url.toString();
};
