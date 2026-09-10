/** Only application paths are accepted, including paths containing a query. */
export function safeAuthRedirect(
  destination: string | null | undefined,
): string {
  if (
    !destination?.startsWith("/") ||
    destination.startsWith("//") ||
    destination.includes("\\") ||
    Array.from(destination).some(
      (character) =>
        character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
    )
  )
    return "/";
  return destination;
}

export function getAuthReturnTo(params: URLSearchParams): string {
  return safeAuthRedirect(
    params.get("returnTo") ||
      params.get("redirect") ||
      params.get("redirect_url"),
  );
}

export function authPageUrl(
  path: string,
  returnTo: string,
  invitationToken?: string | null,
) {
  const params = new URLSearchParams({ returnTo: safeAuthRedirect(returnTo) });
  if (invitationToken) params.set("invitation_token", invitationToken);
  return `${path}?${params}`;
}

export function completeAuth(result: { redirect_url: string }) {
  // A full navigation drops cached data from the previous browser session and
  // lets the backend run admission, terms, and onboarding before app access.
  window.location.href = safeAuthRedirect(result.redirect_url);
}
