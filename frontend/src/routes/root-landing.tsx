import React from "react";
import { redirect } from "react-router";
import { Route } from "./+types/root-landing";
import { getAgentCanvasBannerLink } from "#/components/features/home/home-header/agent-canvas-banner";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { INVITATION_TOKEN_KEY } from "#/hooks/use-invitation";

/**
 * Invitation links land on `/?invitation_token=…` (see
 * `accept_invitation_redirect` in `org_invitations.py`). Save the token here:
 * the settings guards redirect without the query string, so it is gone from
 * the URL before `useInvitation` can read it. Then keep the user in the
 * settings shell, where `root-layout` auto-accepts the saved token, instead
 * of leaving for Canvas before that happens.
 */
export function clientLoader({ request }: Route.ClientLoaderArgs) {
  const { search, searchParams } = new URL(request.url);
  const invitationToken = searchParams.get("invitation_token");
  if (invitationToken) {
    localStorage.setItem(INVITATION_TOKEN_KEY, invitationToken);
    return redirect(`/settings${search}`);
  }
  return null;
}

/**
 * Agent Canvas (served at `${origin}/canvas` next to this app) is the primary
 * surface, so `/` — and therefore the default post-login destination — hard
 * redirects there. `location.replace` keeps `/` out of history; a router
 * redirect would bounce through the SPA's own `canvas/*` route instead.
 */
function getAgentCanvasRedirectUrl(location: Location): string {
  const url = new URL(getAgentCanvasBannerLink(location).url);
  url.search = location.search;
  url.hash = location.hash;
  return url.toString();
}

export default function RootLanding() {
  React.useEffect(() => {
    window.location.replace(getAgentCanvasRedirectUrl(window.location));
  }, []);

  return (
    <div className="flex h-full items-center justify-center">
      <LoadingSpinner size="large" />
    </div>
  );
}
